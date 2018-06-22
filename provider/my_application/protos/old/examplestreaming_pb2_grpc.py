# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
import grpc
from grpc.framework.common import cardinality
from grpc.framework.interfaces.face import utilities as face_utilities

import examplestreaming_pb2 as examplestreaming__pb2


class DataStreamerStub(object):
  """The data service definition.
  """

  def __init__(self, channel):
    """Constructor.

    Args:
      channel: A grpc.Channel.
    """
    self.SendData = channel.unary_stream(
        '/examplestreaming.DataStreamer/SendData',
        request_serializer=examplestreaming__pb2.OK.SerializeToString,
        response_deserializer=examplestreaming__pb2.Message.FromString,
        )
    self.ReceiveData = channel.stream_unary(
        '/examplestreaming.DataStreamer/ReceiveData',
        request_serializer=examplestreaming__pb2.Prediction.SerializeToString,
        response_deserializer=examplestreaming__pb2.OK.FromString,
        )


class DataStreamerServicer(object):
  """The data service definition.
  """

  def SendData(self, request, context):
    """Sends multiple greetings
    """
    context.set_code(grpc.StatusCode.UNIMPLEMENTED)
    context.set_details('Method not implemented!')
    raise NotImplementedError('Method not implemented!')

  def ReceiveData(self, request_iterator, context):
    context.set_code(grpc.StatusCode.UNIMPLEMENTED)
    context.set_details('Method not implemented!')
    raise NotImplementedError('Method not implemented!')


def add_DataStreamerServicer_to_server(servicer, server):
  rpc_method_handlers = {
      'SendData': grpc.unary_stream_rpc_method_handler(
          servicer.SendData,
          request_deserializer=examplestreaming__pb2.OK.FromString,
          response_serializer=examplestreaming__pb2.Message.SerializeToString,
      ),
      'ReceiveData': grpc.stream_unary_rpc_method_handler(
          servicer.ReceiveData,
          request_deserializer=examplestreaming__pb2.Prediction.FromString,
          response_serializer=examplestreaming__pb2.OK.SerializeToString,
      ),
  }
  generic_handler = grpc.method_handlers_generic_handler(
      'examplestreaming.DataStreamer', rpc_method_handlers)
  server.add_generic_rpc_handlers((generic_handler,))
