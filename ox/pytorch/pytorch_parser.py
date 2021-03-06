#----------------------------------------------------------------------------------------------
#  Copyright (c) Microsoft Corporation. All rights reserved.
#  Licensed under the MIT License. See License.txt in the project root for license information.
#----------------------------------------------------------------------------------------------

import logging
import os
import numpy as np
import ox.common.IR.graph_pb2 as graph_pb2
from ox.common.IR.graph_pb2 import NodeDef, GraphDef, DataType
from ox.common.utils import *
from ox.common.DataStructure.parser import Parser
from ox.pytorch.pytorch_graph import PytorchGraph
from ox.pytorch.rewriter.lstm_rewriter import LstmRewriter
import torch
import torchvision

class PytorchParser(Parser):

    layer_map = {
    'onnx::Conv': 'Conv',
    'onnx::Flatten': 'Flatten',
    'onnx::Gemm': 'FullyConnected',
    'onnx::MaxPool': 'Maxpool',
    'onnx::AveragePool': 'Avgpool',
    'onnx::Dropout': 'Dropout',
    'onnx::BatchNormalization': 'BatchNormalization',
    'onnx::Add': 'Add',
    'onnx::Concat': 'Concat',
    'onnx::Relu': 'Relu',
    'onnx::Tanh': 'Tanh',
    'onnx::Sigmoid': 'Sigmoid',
    'onnx::MatMul': 'MatMul',
    'onnx::Mul': 'Mul',
    'onnx::Constant': 'Constant',
    'onnx::Reshape': 'Reshape',
    'aten::reshape': 'Reshape',
    'onnx::Transpose': 'Transpose',
    'onnx::LogSoftmax': 'LogSoftmax',
    'onnx::Slice': 'Slice',
    'onnx::Squeeze': 'Squeeze',
    'onnx::ConvTranspose': 'ConvTranspose'
    }


    ############
    # property #
    ############

    @property
    def src_graph(self):
        return self.pytorch_graph


    ####################
    # Public Functions #
    ####################

    def __init__(self, model_file_name, input_shape):
        super(PytorchParser, self).__init__()
        if not os.path.exists(model_file_name):
            print("Pytorch model file [{}] is not found.".format(model_file_name))
            assert False
        # test

        # cpu: https://github.com/pytorch/pytorch/issues/5286
        try:
            model = torch.load(model_file_name)
        except:
            model = torch.load(model_file_name, map_location='cpu')

        self.weight_loaded = True

        # Build network graph
        self.pytorch_graph = PytorchGraph(model)

        self.input_shape = tuple([1] + input_shape)
        self.pytorch_graph.build(self.input_shape)        

        lstm_rewriter = LstmRewriter(self.pytorch_graph)
        self.pytorch_graph = lstm_rewriter.run()

        self.state_dict = self.pytorch_graph.state_dict
        self.shape_dict = self.pytorch_graph.shape_dict


    def gen_IR(self):
        
        node_set = set()
        for layer in self.src_graph.topological_sort:
            current_node = self.src_graph.get_node(layer)
            onnx_node_type = current_node.type
            if onnx_node_type not in PytorchParser.layer_map:
                logger = logging.getLogger()
                logger.warning("Warning: onnx_node_type '%s' not in PytorchParser.layer_map" % (onnx_node_type))
                continue
            node_type = PytorchParser.layer_map[onnx_node_type]

            node_set.add(node_type)

            if hasattr(self, "rename_" + node_type):
                # print("rename_" + node_type)
                func = getattr(self, "rename_" + node_type)
                func(current_node)
            else:
                print('UNKNOWN: ', node_type)
                self.rename_UNKNOWN(current_node)            

        self.gen_Input()

        return list(node_set)



    def _set_output_shape(self, source_node, IR_node):

        shape = graph_pb2.TensorShape()


        layer_name = source_node.name

        shape_pytorch = self.shape_dict[layer_name]


        new_dim = shape.dim.add()

        # (batch, C, H, W)  & NHWC
        if len(shape_pytorch) == 4:

            if shape_pytorch[0] == 1:
                new_dim.size = -1
            else:
                new_dim.size = shape_pytorch[0]
            for index in [2, 3, 1]:
                new_dim = shape.dim.add()
                dim = shape_pytorch[index]
                new_dim.size = dim if dim else -1
        elif len(shape_pytorch) == 2:
            if shape_pytorch[0] == 1:
                new_dim.size = -1
            else:
                new_dim.size = shape_pytorch[0]
            for _ in range(2):
                new_dim = shape.dim.add()
                new_dim.size = 1
            new_dim = shape.dim.add()
            dim = shape_pytorch[1]
            new_dim.size = dim if dim else -1


        IR_node.attr["_output_shapes"].list.shape.extend([shape])

    ##########
    # Layers #
    ##########
    def rename_UNKNOWN(self, source_node):
        print (source_node.layer)
        print (source_node.layer.data.size())
        assert False
        print("PyTorch parser has not supported operator [%s] with name [%s]."
              % (source_node.type, source_node.name))

    def gen_Input(self):
        IR_node = self.IR_graph.node.add()
        IR_node.name = 'input'
        IR_node.op = "DataInput"

        for node in self.IR_graph.node:
            if node.name in self.src_graph.input_layers:
                if not 'onstant' in self.pytorch_graph.layer_map[node.name]._kind:
                    node.input.append('input')

        assert len(self.input_shape) == 4
        new_dim = IR_node.attr["shape"].shape.dim.add()
        if self.input_shape[0] == 1:
            new_dim.size = -1
        else:
            new_dim.size = self.input_shape[0]
        for index in [2, 3, 1]:
            new_dim = IR_node.attr["shape"].shape.dim.add()
            new_dim.size = self.input_shape[index]

        shape = graph_pb2.TensorShape()
        new_dim = shape.dim.add()
        shape_pytorch = self.input_shape

        if len(shape_pytorch) == 4:

            if shape_pytorch[0] == 1:
                new_dim.size = -1
            else:
                new_dim.size = shape_pytorch[0]
            for index in [2, 3, 1]:
                new_dim = shape.dim.add()
                dim = shape_pytorch[index]
                new_dim.size = dim if dim else -1
        elif len(shape_pytorch) == 2:
            if shape_pytorch[0] == 1:
                new_dim.size = -1
            else:
                new_dim.size = shape_pytorch[0]
            for _ in range(2):
                new_dim = shape.dim.add()
                new_dim.size = 1
            new_dim = shape.dim.add()
            dim = shape_pytorch[1]
            new_dim.size = dim if dim else -1


        IR_node.attr["_output_shapes"].list.shape.extend([shape])

    def rename_Constant(self, source_node):
        IR_node = self.IR_graph.node.add()
        IR_node.name = source_node.name
        IR_node.op = "Constant"
        assign_IRnode_values(IR_node, source_node.attrs)
        self._set_output_shape(source_node, IR_node)

    def rename_Conv(self, source_node):

        attr = source_node.attrs
        kwargs = dict()

        # dilation
        if 'dilations' in attr:
            kwargs['dilations'] = [1] + attr['dilations'] + [1]
        else:
            kwargs['dilations'] = [1] + [1, 1] + [1]

        if len(attr['pads']) == 4:
            kwargs['pads'] = [0] + attr['pads'][0:2] + [0, 0] + attr['pads'][2:] + [0]
        elif len(attr['pads']) == 2:
            kwargs['pads'] = ( [0] + attr['pads'][0:2] + [0] ) *2

        if 'strides' not in attr:
            kwargs['strides'] = [1] + [1, 1] + [1]
        else:
            kwargs['strides'] = [1] + attr['strides'] + [1]

        kwargs['group'] = attr['group']

        bias_name = '{0}.bias'.format(source_node.weights_name)
        weights_name = '{0}.weight'.format(source_node.weights_name)

        weight = self.state_dict[weights_name].numpy()

        dim = weight.ndim - 2

        IR_node = self._convert_identity_operation(source_node, new_op="Conv")
        weight = np.transpose(weight, list(range(2, dim + 2)) + [1, 0])

        self.set_weight(source_node.name, 'weights', weight)
        kwargs['kernel_shape'] = list(weight.shape)
        kwargs['kernel_shape'][-2] *= kwargs['group']

        # print(kwargs['kernel_shape'], kwargs['group'])

        # handle bias
        if bias_name in self.state_dict:
            bias = self.state_dict[bias_name].numpy()
            self.set_weight(source_node.name, 'bias', bias)
            kwargs['use_bias'] = True
        else:
            kwargs['use_bias'] = False


        assign_IRnode_values(IR_node, kwargs)

    def rename_ConvTranspose(self, source_node):
        # print('Transpose:', source_node.attrs)

        # IR_node = self._convert_identity_operation(source_node, new_op="ConvTranspose")
        attr = source_node.attrs
        kwargs = dict()
        # dilations [1, 1]
        # group 1
        # kernel shape [4, 4]
        # pads [0, 0, 0, 0]
        # strides [2, 2]

        # dilation
        if 'dilations' in attr:
            kwargs['dilations'] = [1] + attr['dilations'] + [1]
        else:
            kwargs['dilations'] = [1] + [1, 1] + [1]

        if len(attr['pads']) == 4:
            kwargs['pads'] = [0] + attr['pads'][0:2] + [0, 0] + attr['pads'][2:] + [0]
        elif len(attr['pads']) == 2:
            kwargs['pads'] = ( [0] + attr['pads'][0:2] + [0] ) *2

        if 'strides' not in attr:
            kwargs['strides'] = [1] + [1, 1] + [1]
        else:
            kwargs['strides'] = [1] + attr['strides'] + [1]

        kwargs['group'] = attr['group']

        bias_name = '{0}.bias'.format(source_node.weights_name)
        weights_name = '{0}.weight'.format(source_node.weights_name)

        weight = self.state_dict[weights_name].numpy()

        dim = weight.ndim - 2

        IR_node = self._convert_identity_operation(source_node, new_op="ConvTranspose")
        weight = np.transpose(weight, list(range(2, dim + 2)) + [1, 0])

        self.set_weight(source_node.name, 'weights', weight)
        kwargs['kernel_shape'] = list(weight.shape)
        kwargs['kernel_shape'][-2] *= kwargs['group']

        # print(kwargs['kernel_shape'], kwargs['group'])

        # handle bias
        if bias_name in self.state_dict:
            bias = self.state_dict[bias_name].numpy()
            self.set_weight(source_node.name, 'bias', bias)
            kwargs['use_bias'] = True
        else:
            kwargs['use_bias'] = False


        assign_IRnode_values(IR_node, kwargs)


    def rename_BatchNormalization(self, source_node):
        # TODO
        # output_shape

        IR_node = self._convert_identity_operation(source_node, new_op="BatchNorm")


        attr = source_node.attrs
        # epsilon
        IR_node.attr['epsilon'].f = attr['epsilon']


        bias_name = '{0}.bias'.format(source_node.weights_name)
        weights_name = '{0}.weight'.format(source_node.weights_name)
        mean_name = '{0}.running_mean'.format(source_node.weights_name)
        var_name = '{0}.running_var'.format(source_node.weights_name)



        if bias_name in self.state_dict:
            beta = self.state_dict[bias_name].numpy()
            IR_node.attr['bias'].b = True
        else:
            IR_node.attr['bias'].b = False

        if weights_name in self.state_dict:
            gamma = self.state_dict[weights_name].numpy()
            IR_node.attr['scale'].b = True
        else:
            IR_node.attr['scale'].b = False

        mean = self.state_dict[mean_name].numpy()
        variance = self.state_dict[var_name].numpy()

        if IR_node.attr['scale'].b:
            self.set_weight(source_node.name, "scale", gamma)

        if IR_node.attr['bias'].b:
            self.set_weight(source_node.name, "bias", beta)

        # mean
        self.set_weight(source_node.name, "mean", mean)

        # var
        self.set_weight(source_node.name, "var", variance)

    def rename_Reshape(self, source_node):
        # print('Reshape:', source_node.attrs, source_node.type)
        IR_node = self._convert_identity_operation(source_node, new_op="Reshape")
        if "shape" in source_node.attrs:
            kwargs = dict()
            kwargs["shape"] = source_node.attrs["shape"]
            assign_IRnode_values(IR_node, kwargs)
        # if len(IR_node.input) > 1:
        #     parent = self.src_graph.get_parent(source_node.name, [1])
        #     shape_list = parent.attrs
        #     print(list(range(2, 2 + 2)) + [1, 0])
        #     shape_list = np.transpose(shape_list, list(range(2, 2 + 2)) + [1, 0])
        #     print(shape_list)
        #     print(self.pytorch_graph.get_node(IR_node.input[1]).attrs)

    def rename_Transpose(self, source_node):
        # print('Transpose:', source_node.attrs)
        IR_node = self._convert_identity_operation(source_node, new_op="Transpose")
        if 'perm_list' in source_node.attrs:
            kwargs = dict()
            kwargs['perm_list'] = source_node.attrs['perm_list']
            assign_IRnode_values(IR_node, kwargs)
        
        if 'perm' in source_node.attrs:
            kwargs = dict()
            kwargs['perm'] = source_node.attrs['perm']
            assign_IRnode_values(IR_node, kwargs)
 
    def rename_Slice(self, source_node):
        # print('Transpose:', source_node.attrs)
        IR_node = self._convert_identity_operation(source_node, new_op="Slice")
        kwargs = dict()
        kwargs['shrink_axis_mask'] = source_node.attrs['axes'][0]
        kwargs['starts'] = source_node.attrs['starts']
        kwargs['ends'] = source_node.attrs['ends']
        if 'input_from_param' in source_node.attrs:
            input_from_param = source_node.attrs['input_from_param']
            if isinstance(input_from_param, list):
                concat_numpy = np.array([])
                concat_name = ""
                for param_name in input_from_param:
                    assert isinstance(param_name, str), "set 'input_from_param' as illegal type."
                    concat_name += ("_" + param_name)
                    param_numpy = self.state_dict[param_name].numpy()
                    concat_numpy = np.concatenate((concat_numpy, param_numpy)).astype(param_numpy.dtype)
                kwargs['input_from_param'] = concat_name
                self.set_weight(source_node.name, concat_name, concat_numpy)
            else:
                param_name = input_from_param
                assert isinstance(input_from_param, str), "set 'input_from_param' as illegal type."
                kwargs['input_from_param'] = param_name
                param_numpy = self.state_dict[param_name].numpy()
                self.set_weight(source_node.name, param_name, param_numpy)
        assign_IRnode_values(IR_node, kwargs)        

    def rename_LogSoftmax(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op="LogSoftmax")

    def rename_Relu(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op="Relu")

    def rename_Tanh(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op="Tanh")

    def rename_Sigmoid(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op="Sigmoid")

    def rename_Mul(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op="Mul")

    def rename_Maxpool(self, source_node):
        attr = source_node.attrs
        kwargs = dict()
        kwargs['strides'] = [1] + attr['strides'] + [1]
        if 'dilations' not in attr:
            kwargs['dilations'] = [1] + [1, 1] + [1]
        else:
            kwargs['dilations'] = [1] + attr['dilations'] + [1]
        kwargs['pads'] = [0] + attr['pads'][0:2] + [0, 0] + attr['pads'][2:] + [0]
        kwargs['kernel_shape'] = [1] + attr['kernel_shape'] + [1]
        IR_node = self._convert_identity_operation(source_node, new_op="Pool")

        kwargs['pooling_type'] = 'MAX'

        assign_IRnode_values(IR_node, kwargs)

    def rename_Avgpool(self, source_node):
        # print('Avgpool:', source_node.attrs)
        attr = source_node.attrs
        kwargs = dict()
        kwargs['strides'] = [1] + attr['strides'] + [1]
        if 'dilations' not in attr:
            kwargs['dilations'] = [1] + [1, 1] + [1]
        else:
            kwargs['dilations'] = [1] + attr['dilations'] + [1]
        kwargs['pads'] = [0] + attr['pads'][0:2] + [0, 0] + attr['pads'][2:] + [0]
        kwargs['kernel_shape'] = [1] + attr['kernel_shape'] + [1]
        IR_node = self._convert_identity_operation(source_node, new_op="Pool")

        kwargs['pooling_type'] = 'AVG'

        assign_IRnode_values(IR_node, kwargs)

    def rename_Flatten(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op="Flatten")

    def rename_FullyConnected(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op="FullyConnected")

        bias_name = '{0}.bias'.format(source_node.weights_name)
        weights_name = '{0}.weight'.format(source_node.weights_name)


        W = self.state_dict[weights_name].numpy().transpose()
        input_channels, output_channels = W.shape

        # Kit weight tranpose
        # weight: N x M -> C x H x W x M -> H x W x C x M -> N x M
        if self.weight_loaded:
            parent = self.src_graph.get_parent(source_node.name, [0])
            while parent.type == 'onnx::Flatten' or parent.type == 'onnx::Dropout':
                parent = self.src_graph.get_parent(parent.name, [0])
            if len(self.shape_dict[parent.name]) == 4:
                #
                original_shape = W.shape
                channel_first_list = self.shape_dict[parent.name][1:]
                dim = len(channel_first_list) + 1
                weight = W.reshape(channel_first_list + [original_shape[1]])
                assert dim > 2
                weight = weight.transpose(list(range(1, dim-1)) + [0, dim-1])
                W = weight.reshape(original_shape)

        # weights
        self.set_weight(source_node.name, 'weights', W )

        # use_bias
        if bias_name in self.state_dict:
            IR_node.attr['use_bias'].b = True
            bias = self.state_dict[bias_name].numpy()
            self.set_weight(source_node.name, 'bias', bias )
        else:
            IR_node.attr['use_bias'].b = False

        # units
        IR_node.attr['units'].i = output_channels


    def rename_Dropout(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op='Dropout')
        IR_node.attr['keep_prob'].f = source_node.attrs['ratio']

    def rename_Concat(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op='Concat')
        if source_node.attrs['axis'] == 1:
            IR_node.attr['axis'].i = len(self.shape_dict[source_node.name]) - 1
        else:
            IR_node.attr['axis'].i = source_node.attrs['axis']

    def rename_Add(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op='Add')


    def rename_MaxPool2d(self, source_node):
        self._convert_pooling(source_node)


    def rename_View(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op='Reshape')
        assign_IRnode_values(IR_node, {'shape' : list(source_node.get_attr('new_sizes'))[1:]})


    def rename_Addmm(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op='FullyConnected')
        kwargs = dict()

        # handle weight
        weight = source_node.get_attr('next_functions')[2][0].next_functions[0][0].variable.data.numpy()
        weight = np.transpose(weight)
        kwargs['units'] = weight.shape[1]
        self.set_weight(source_node.name, 'weights', weight)

        # handle bias
        if source_node.get_attr('next_functions')[0][0]:
            bias = source_node.get_attr('next_functions')[0][0].variable.data.numpy()
            kwargs['use_bias'] = True
            self.set_weight(source_node.name, 'bias', weight)

        assign_IRnode_values(IR_node, kwargs)

        print(IR_node)

    def rename_Squeeze(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op='Squeeze')

    def rename_MatMul(self, source_node):
        IR_node = self._convert_identity_operation(source_node, new_op='MatMul')

    ####################
    # Helper Functions #
    ####################

    @staticmethod
    def _copy_and_reop(source_node, IR_node, new_op = None):
        if new_op == None: new_op = source_node.type
        IR_node.name = source_node.name
        IR_node.op = new_op


    def _convert_identity_operation(self, source_node, in_edge_count = None, new_op = None):
        IR_node = self.IR_graph.node.add()
        PytorchParser._copy_and_reop(source_node, IR_node, new_op)
        self.convert_inedge(source_node, IR_node, 0, in_edge_count)
        self._set_output_shape(source_node, IR_node)
        return IR_node

    def _convert_pooling(self, source_node):
        kwargs = dict()
        kwargs['strides'] = [1] + list(source_node.get_attr('stride')) + [1]
        kwargs['dilations'] = [1] + list(source_node.get_attr('dilation')) + [1]
        kwargs['pads'] = ([0] + list(source_node.get_attr('padding')) + [0]) * 2
        kwargs['kernel_shape'] = [1] + list(source_node.get_attr('kernel_size')) + [1]
        IR_node = self._convert_identity_operation(source_node, new_op="Pool")

        if source_node.name.startswith('Max'):
            kwargs['pooling_type'] = 'MAX'
        elif source_node.name.startswith('Avg'):
            kwargs['pooling_type'] = 'AVG'
        else:
            raise ValueError('Unknown pooling type')

        assign_IRnode_values(IR_node, kwargs)
