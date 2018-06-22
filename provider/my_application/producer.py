import threading
import logging
import time
from dateutil import parser
import datetime
import time
from kafka import KafkaConsumer, KafkaProducer, SimpleClient
import csv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
import json
import sys
import sqlalchemy
from repositories.CompetitionRepository import CompetitionRepository, Competition, Datastream, DatastreamRepository
from repositories.KafkaToMongo import ConsumerToMongo
from repositories.BaselineToMongo import BaselineToMongo
from spark_evaluation import SparkEvaluator
from evaluator import Evaluator
from bson import json_util
import json
from consumer import DataStreamerServicer
from stream_server import StreamServer
import os
from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from pyspark.sql.types import *
from pyspark.sql.types import _infer_type
from baseline_client import baselineClient

os.environ['PYSPARK_SUBMIT_ARGS'] = '--packages org.apache.spark:spark-sql-kafka-0-10_2.11:2.3.1 pyspark-shell'
spark = SparkSession.builder.appName("Kafka_structured_streaming").getOrCreate()
# from apscheduler.schedulders.background.BackgroundScheduler import remove_job


_ONE_DAY_IN_SECONDS = 60 * 60 * 24


with open('config.json') as json_data_file:
    config = json.load(json_data_file)

SERVER_HOST = config['SERVER_HOST']  # 172.22.0.2:9092
_SQL_HOST = config['SQL_HOST']
_SQL_DBNAME = config['SQL_DBNAME']
_UPLOAD_REPO = config['UPLOAD_REPO']
_STREAM_REPO = config['STREAM_DATA_FILE']

_COMPETITION_REPO = CompetitionRepository(_SQL_HOST, _SQL_DBNAME)
_DATASTREAM_REPO = DatastreamRepository(_SQL_HOST, _SQL_DBNAME)

_gRPC_SERVER = StreamServer()


def _create_competition(competition, competition_config, test_user):
    threading.Thread(target=_create_competition_thread, args=(competition, competition_config)).start()
    threading.Thread(target=_create_consumer, args=(competition,)).start()
    threading.Thread(target=_create_mongo_sink_consumer,
                     args=(competition.name.lower().replace(" ", "") + 'predictions',)).start()
    time.sleep(2)
    threading.Thread(target=_create_baseline, args=(competition, competition_config)).start()
    train_schema, prediction_schema = _create_json_schema(competition, competition_config)
    spark_evaluator = SparkEvaluator(spark, SERVER_HOST, competition,
                                     train_schema, prediction_schema)
    spark_evaluator.main()


def _create_json_schema(competition, competition_config):
    datastream = _DATASTREAM_REPO.get_datastream_by_id(competition.datastream_id)
    file_name = datastream.file_path
    file_path = os.path.join(_UPLOAD_REPO, _STREAM_REPO, file_name)
    target_dict = {}

    with open(file_path, 'r') as csvfile:
        datareader = csv.reader(csvfile, delimiter=',', quotechar='|')
        data = list(datareader)
        header = data[0]
        data_sample = data[1]
        for i in range(0, len(header)):
            for key in competition_config.keys():
                x = header[i].lower().replace(' ', '')  # Field name
                y = str(key.lower().replace(' ', ''))  # Key
                if x == y:
                    # field = header[i].replace(" ", "")
                    target_dict[header[i]] = data_sample[i]
        print("Data sample: ", data_sample)
        print("Target dict: ", target_dict)

    # timestamp_schema = StructType().add("$date", LongType(), False)

    train_schema = StructType()\
        .add("Deadline", StringType(), False)\
        .add("Released", StringType(), False)\
        .add("competition_id", IntegerType(), False)\
        .add("rowID", IntegerType(), False)

    prediction_schema = StructType()\
        .add("rowID", IntegerType(), False)\
        .add("submitted_on", StringType(), False)\
        .add("competition_id", IntegerType(), False)\
        .add("user_id", IntegerType(), False)

    for key, value in target_dict.items():
        # test_schema.add(key, _infer_type(value))
        train_schema.add(str(key), StringType(), False)
        prediction_schema.add(str(key).replace(" ", ""), StringType(), False)

    return train_schema, prediction_schema  # , test_schema, init_schema


def _create_evaluation_engine(competition_id, config, evaluation_time_interval):
    threading.Thread(target=_create_evaluation_job, args=(competition_id, config, evaluation_time_interval)).start()


def _create_competition_thread(competition, competition_config):
    # print(competition, datetime.datetime.now())
    print("usao u create competition tred")
    producer = Producer(SERVER_HOST)  # 172.22.0.2:9092

    producer.create_competition(competition, competition_config)
    # producer.producer.flush()


def _create_consumer(competition):
    streamer = DataStreamerServicer(SERVER_HOST, competition)  # 172.22.0.2:9092
    _gRPC_SERVER.add_server(streamer, competition)


def _create_mongo_sink_consumer(topic):
    mongo_writer = ConsumerToMongo(SERVER_HOST, topic)
    mongo_writer.write('data')


def _create_baseline(competition, competition_config):
    topic = competition.name.lower().replace(" ", "") + 'predictions'
    baseline = BaselineToMongo(SERVER_HOST, topic, competition, competition_config)
    baseline.write()


def _create_evaluation_job(competition_id, config, evaluation_time_interval):
    # print(competition_id, datetime.datetime.now())
    # print("Started evaluation", threading.active_count())
    Evaluator.evaluate(competition_id, config, evaluation_time_interval)


class Producer:
    daemon = True
    producer = None

    def __init__(self, server):
        self.producer = KafkaProducer(bootstrap_servers=server)  # Create producer
        self.client = SimpleClient(SERVER_HOST)  # Create client

    # message must be in byte format
    def send(self, topic, message):

        self.producer.send(topic, message)  # Sending messages to a certain topic

    def main(self, topic, file_path, initial_batch_size, initial_training_time, batch_size, time_interval,
             predictions_time_interval, competition_config, spark_topic, competition_id, with_timestamp=False, data_format='csv'):

        initial_batch = []
        items = []
        predictions = []

        # Creating file path: ../local/data/uploads/stream_data_file
        file_path = os.path.join(_UPLOAD_REPO, _STREAM_REPO, file_path)

        # _UPLOAD_REPO+ file_path
        # Read csv file
        if data_format == 'csv':
            try:
                with open(file_path, 'r') as csvfile:
                    datareader = csv.reader(csvfile, delimiter=',', quotechar='|')
                    data = list(datareader)
                    nb_rows = len(data)
                    header = data[0]
                    rowID = 1
                    # Process initial batch
                    for row in range(1, initial_batch_size + 1):
                        # If the row is not empty ???
                        if not self.is_not_empty(data[row]):

                            values = {}
                            values['tag'] = 'INIT'
                            values['rowID'] = rowID
                            rowID = rowID + 1
                            for i in range(0, len(data[row])):
                                values[header[i]] = data[row][i]

                            initial_batch.append(values)
                    # After the initial batch
                    for row in range(initial_batch_size + 1, nb_rows - 1):
                        # If row is not empty ???
                        if not self.is_not_empty(data[row]):
                            # Create a dictionaries: values = {'rowID': rowID}
                            # prediction = {'rowID': rowID}
                            try:
                                values = {}
                                values['rowID'] = rowID
                                prediction = {}
                                prediction['rowID'] = rowID
                                rowID = rowID + 1
                                # print("Competition config: ", competition_config)
                                # For every field in row
                                for i in range(0, len(data[row])):
                                    # keys in competition_config  ??? targets?
                                    for key in competition_config.keys():
                                        x = header[i].lower().replace(' ', '')  # Field name
                                        y = str(key.lower().replace(' ', ''))  # Key
                                        # If field name == target = > prediction
                                        if x == y:
                                            prediction[header[i]] = data[row][i]
                                        # If field name != target = > values
                                        else:
                                            values[header[i]] = data[row][i]

                                # add values to items list and prediction to predictions list
                                items.append(values)
                                predictions.append(prediction)

                            except Exception as e:
                                print('error')

            except IOError as e:
                print("could not open file" + file_path)

        for item in initial_batch:
            try:
                # Send row by row from initial batch as json
                self.send(topic, json.dumps(item, default=json_util.default).encode('utf-8'))
                # print("Init item:", item)
            except Exception as e:
                # Check if topic exists, if not, create it and then send
                self.client.ensure_topic_exists(topic)
                self.producer.send(topic, json.dumps(item, default=json_util.default).encode('utf-8'))
                # print("Init item:", item)
        # After sending initial batch, sleep for initial training time
        time.sleep(int(initial_training_time))
        # Creating lists of batch size, one for test items with just values and second with predictions for training
        test_groups = list(self.chunker(items, batch_size))
        train_groups = list(self.chunker(predictions, batch_size))

        # print(len(items))

        i = -1

        deadline = datetime.datetime.now()
        # Test then train  ???
        deadline = deadline + datetime.timedelta(seconds=initial_training_time)
        # Accessing each group in the list test_groups
        for group in test_groups:

            released_at = datetime.datetime.now()
            if i != -1:
                # In parallel accessing the predictions
                train_group = train_groups[i]
                # Adding tag, deadline and released at to every item in train group / prediction
                for item in train_group:
                    item['tag'] = 'TRAIN'
                    deadline = released_at + datetime.timedelta(seconds=int(predictions_time_interval))
                    item['Deadline'] = deadline
                    item['Released'] = released_at
                    # item
                    try:
                        self.send(topic, json.dumps(item, default=json_util.default).encode('utf-8'))
                        # print("Train item:", item)
                    except Exception as e:
                        self.client.ensure_topic_exists(topic)
                        self.producer.send(topic, json.dumps(item, default=json_util.default).encode('utf-8'))
                    del item['Deadline']
                    del item['Released']
                    del item['tag']
                    item['Deadline'] = deadline.strftime("%Y-%m-%d %H:%M:%S")
                    item['Released'] = released_at.strftime("%Y-%m-%d %H:%M:%S")
                    item['competition_id'] = competition_id
                    try:
                        self.send(spark_topic, json.dumps(item, default=json_util.default).encode('utf-8'))
                    except Exception as e:
                        self.client.ensure_topic_exists(spark_topic)
                        self.send(spark_topic, json.dumps(item, default=json_util.default).encode('utf-8'))

                        # print("Train item:", item)
            # for item in test group add tag, deadline and released
            for item in group:
                item['tag'] = 'TEST'
                item['Deadline'] = released_at + datetime.timedelta(seconds=int(predictions_time_interval))
                item['Released'] = released_at
                # Sending testing items
                try:
                    self.send(topic, json.dumps(item, default=json_util.default).encode('utf-8'))
                    # print("Test item:", item)
                except Exception as e:
                    self.client.ensure_topic_exists(topic)
                    self.producer.send(topic, json.dumps(item, default=json_util.default).encode('utf-8'))
                    # print("Test item:", item)

            i = i + 1

            time.sleep(time_interval)

        # Sending last train batch
        released_at = datetime.datetime.now()
        for prediction in train_groups[len(train_groups) - 1]:
            prediction['tag'] = 'TRAIN'
            deadline = released_at + datetime.timedelta(seconds=int(predictions_time_interval))
            prediction['Deadline'] = deadline
            prediction['Released'] = released_at
            try:
                self.send(topic, json.dumps(prediction, default=json_util.default).encode('utf-8'))
                # print("Train item:", item)
            except Exception as e:
                self.client.ensure_topic_exists(topic)
                self.producer.send(topic, json.dumps(prediction, default=json_util.default).encode('utf-8'))
            del prediction['Deadline']
            del prediction['Released']
            del prediction['tag']
            prediction['Deadline'] = deadline.strftime("%Y-%m-%d %H:%M:%S")
            prediction['Released'] = released_at.strftime("%Y-%m-%d %H:%M:%S")
            prediction['competition_id'] = competition_id
            try:
                self.send(spark_topic, json.dumps(prediction, default=json_util.default).encode('utf-8'))
                # print("Train item:", item)
            except Exception as e:
                self.client.ensure_topic_exists(topic)
                self.producer.send(spark_topic, json.dumps(prediction, default=json_util.default).encode('utf-8'))

                # print("Train item:", item)

        time.sleep(time_interval)

        self.producer.flush()

    # Creating chunks of batch size
    def chunker(self, seq, size):
        return (seq[pos:pos + size] for pos in range(0, len(seq), size))

    # Check if the row is empty ??
    def is_not_empty(self, row):  # is_empty
        return all(item == "" for item in row)

    # Creating competition, competition_config ???
    def create_competition(self, competition, competition_config):
        print("Came here, create competition: ", competition.name)
        datastream = _DATASTREAM_REPO.get_datastream_by_id(competition.datastream_id)
        self.main(
            topic=competition.name.lower().replace(" ", ""),
            file_path=datastream.file_path,
            initial_batch_size=competition.initial_batch_size,
            initial_training_time=competition.initial_training_time,
            batch_size=competition.batch_size,
            time_interval=competition.time_interval,
            predictions_time_interval=competition.predictions_time_interval,
            competition_config=competition_config,
            spark_topic=competition.name.lower().replace(" ", "")+'spark_train',
            competition_id=competition.competition_id)


class Scheduler:

    def __init__(self):
        jobstores = {
            'default': MongoDBJobStore(database='apscheduler', collection='jobs', host='172.22.0.3', port=27017)}
        self.scheduler = BackgroundScheduler(jobstores=jobstores)
        # print("Version: ", sys.version)

    def schedule_competition(self, competition, competition_config, test_user):
        competition_job_id = str(competition.name)
        start_date = competition.start_date
        end_date = competition.end_date
        year = start_date.year
        month = start_date.month
        day = start_date.day
        hour = start_date.hour
        minute = start_date.minute
        second = start_date.second

        publish_job = self.scheduler.add_job(_create_competition, trigger='cron', year=year, month=month, day=day, hour=hour,
                                             minute=minute, second=second, start_date=start_date, end_date=end_date,
                                             args=[competition, competition_config, test_user], id=competition.name)

        job_id_to_remove = publish_job.id

        if competition.predictions_time_interval < 5:
            evaluation_time_interval = 5
        else:
            evaluation_time_interval = competition.predictions_time_interval

        start_date = competition.start_date + datetime.timedelta(
            seconds=competition.initial_training_time + competition.time_interval + evaluation_time_interval + 2)
        # print("Start date: ", start_date)

        # config = {'Valeurs': ['MAPE']}
        evaluation_job = self.scheduler.add_job(_create_evaluation_engine, trigger='interval',
                                                seconds=evaluation_time_interval, start_date=start_date,
                                                end_date=competition.end_date,
                                                args=(competition.competition_id, competition_config,
                                                      evaluation_time_interval),
                                                id=str(competition.name + 'evaluation'))

        print("Created evaulation job")
        jobs = self.scheduler.get_jobs()

    def start(self):
        # print("Scheduler started!")
        self.scheduler.start()

    def _stop_competition(self, job_id, end_date):
        """
        def _delete_job(job_id):
            print('stop',self.scheduler.get_jobs())
            for job in self.scheduler.get_jobs():
                print(job.id, job_id)
                if job.id == job_id:
                    self.schedulder.remove_job(job)
                    print ("No more " + job_id)"""
        self.scheduler.add_job(self.scheduler.remove_job, 'date', run_date=end_date, args=[job_id])





