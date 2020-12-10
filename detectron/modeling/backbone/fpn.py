# Copyright (c) Facebook, Inc. and its affiliates.
import math
import fvcore.nn.weight_init as weight_init
import torch.nn.functional as F
import torch 
from torch import nn

from detectron2.layers import Conv2d, ShapeSpec, get_norm

from .backbone import Backbone
from .build import BACKBONE_REGISTRY
from .resnet import build_resnet_backbone

from .ftt import FTT_get_p3pr

__all__ = ["build_resnet_fpn_backbone", 
            "FPN"]


class FPN(Backbone):
    """
    This module implements :paper:`FPN`.
    It creates pyramid features built on top of some input feature maps.
    """

    def __init__(
        self, bottom_up, in_features, out_channels, norm="", top_block=None, fuse_type="sum"
    ):
        """
        Args:
            bottom_up (Backbone): module representing the bottom up subnetwork.
                Must be a subclass of :class:`Backbone`. The multi-scale feature
                maps generated by the bottom up network, and listed in `in_features`,
                are used to generate FPN levels.
            in_features (list[str]): names of the input feature maps coming
                from the backbone to which FPN is attached. For example, if the
                backbone produces ["res2", "res3", "res4"], any *contiguous* sublist
                of these may be used; order must be from high to low resolution.
            out_channels (int): number of channels in the output feature maps.
            norm (str): the normalization to use.
            top_block (nn.Module or None): if provided, an extra operation will
                be performed on the output of the last (smallest resolution)
                FPN output, and the result will extend the result list. The top_block
                further downsamples the feature map. It must have an attribute
                "num_levels", meaning the number of extra FPN levels added by
                this block, and "in_feature", which is a string representing
                its input feature (e.g., p5).
            fuse_type (str): types for fusing the top down features and the lateral
                ones. It can be "sum" (default), which sums up element-wise; or "avg",
                which takes the element-wise mean of the two.
        """
        #print("\n\n CONFIRMING THAT NEW FPN IS PRINTED\n\n")
        super(FPN, self).__init__()
        assert isinstance(bottom_up, Backbone)
        assert in_features, in_features

        #print(in_features) #['res2', 'res3', 'res4', 'res5', 'res6']
        #print(out_channels) #256
        #print(top_block) -> LastLevelMaxPool()
        #print(fuse_type) -> sum

        # Feature map strides and channels from the bottom up network (e.g. ResNet)
        input_shapes = bottom_up.output_shape()
        #print(input_shapes)
        # {'res2': ShapeSpec(channels=256, height=None, width=None, stride=4), 'res3': ShapeSpec(channels=512, height=None, width=None, stride=8), 'res4': ShapeSpec(channels=512, height=None, width=None, stride=16), 'res5': ShapeSpec(channels=1024, height=None, width=None, stride=32), 'res6': ShapeSpec(channels=2048, height=None, width=None, stride=64)}
        strides = [input_shapes[f].stride for f in in_features]
        in_channels_per_feature = [input_shapes[f].channels for f in in_features]
        #print(in_channels_per_feature) -> [256, 512, 512, 1024, 2048]

        _assert_strides_are_log2_contiguous(strides)
        lateral_convs = []
        output_convs = []

        use_bias = norm == ""
        for idx, in_channels in enumerate(in_channels_per_feature):
            lateral_norm = get_norm(norm, out_channels)
            output_norm = get_norm(norm, out_channels)

            lateral_conv = Conv2d(
                in_channels, out_channels, kernel_size=1, bias=use_bias, norm=lateral_norm
            )
            output_conv = Conv2d(
                out_channels,
                out_channels,
                kernel_size=3,
                stride=1,
                padding=1,
                bias=use_bias,
                norm=output_norm,
            )
            weight_init.c2_xavier_fill(lateral_conv)
            weight_init.c2_xavier_fill(output_conv)
            stage = int(math.log2(strides[idx]))
            self.add_module("fpn_lateral{}".format(stage), lateral_conv)
            self.add_module("fpn_output{}".format(stage), output_conv)
            lateral_convs.append(lateral_conv)
            output_convs.append(output_conv)
        #print(lateral_convs) #-> [Conv2d(256, 256, kernel_size=(1, 1), stride=(1, 1)), Conv2d(512, 256, kernel_size=(1, 1), stride=(1, 1)), Conv2d(512, 256, kernel_size=(1, 1), stride=(1, 1)), Conv2d(1024, 256, kernel_size=(1, 1), stride=(1, 1)), Conv2d(2048, 256, kernel_size=(1, 1), stride=(1, 1))]
        #print(output_convs) #-> [Conv2d(256, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)), Conv2d(256, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)), Conv2d(256, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)), Conv2d(256, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1)), Conv2d(256, 256, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1))]
        
        # Place convs into top-down order (from low to high resolution)
        # to make the top-down computation in forward clearer.
        self.out_channels = out_channels
        self.norm = norm
        self.lateral_convs = lateral_convs[::-1]
        self.output_convs = output_convs[::-1]
        self.top_block = top_block
        self.in_features = in_features
        self.bottom_up = bottom_up
        # Return feature names are "p<stage>", like ["p2", "p3", ..., "p6"]
        self._out_feature_strides = {"p{}".format(int(math.log2(s))): s for s in strides}
        #print(self._out_feature_strides) -> {'p2': 4, 'p3': 8, 'p4': 16, 'p5': 32, 'p6': 64}
        
        # top block output feature maps.
        if self.top_block is not None:
            for s in range(stage, stage + self.top_block.num_levels):
                self._out_feature_strides["p{}".format(s + 1)] = 2 ** (s + 1)

        #print(self._out_feature_strides)# -> {'p2': 4, 'p3': 8, 'p4': 16, 'p5': 32, 'p6': 64, 'p7': 128}

        self._out_features = list(self._out_feature_strides.keys())
        self._out_feature_channels = {k: out_channels for k in self._out_features}
        # self.ftt = FTT(self, ['p2', 'p3'], out_channels)
        #print(self._out_feature_channels) -> {'p2': 256, 'p3': 256, 'p4': 256, 'p5': 256, 'p6': 256, 'p7': 256}
        self._size_divisibility = strides[-1]
        assert fuse_type in {"avg", "sum"}
        self._fuse_type = fuse_type


        # tuple of (conv2d, conv2d, iter)
        def create_convs(num_channels, iter=3):
            conv1 = Conv2d(
            num_channels,
            num_channels,
            kernel_size=1,
            bias=False,
            norm=get_norm(norm, num_channels),
            )

            conv2 = Conv2d(
            num_channels,
            num_channels,
            kernel_size=1,
            bias=False,
            norm=get_norm(norm, num_channels),
            )
            return (conv1, conv2, iter)
    

    @property
    def size_divisibility(self):
        return self._size_divisibility

    def forward(self, x):
        """
        Args:
            input (dict[str->Tensor]): mapping feature map name (e.g., "res5") to
                feature map tensor for each feature level in high to low resolution order.

        Returns:
            dict[str->Tensor]:
                mapping from feature map name to FPN feature map tensor
                in high to low resolution order. Returned feature names follow the FPN
                paper convention: "p<stage>", where stage has stride = 2 ** stage e.g.,
                ["p2", "p3", ..., "p6"].
        """
        # Reverse feature maps into top-down order (from low to high resolution)
        
        # x.shape = torch.Size([2, 3, 832, 1216]), where 2 is the batch size
        # x[0] = [3, 832, 1216] -> the image

        #print("\nshape of feature map: ",x.shape,"\n\n")

        bottom_up_features = self.bottom_up(x)
        x = [bottom_up_features[f] for f in self.in_features[::-1]]
        results = []
        prev_features = self.lateral_convs[0](x[0])
        results.append(self.output_convs[0](prev_features))
        for features, lateral_conv, output_conv in zip(
            x[1:], self.lateral_convs[1:], self.output_convs[1:]
        ):
            top_down_features = F.interpolate(prev_features, scale_factor=2, mode="nearest")
            lateral_features = lateral_conv(features)
            # print("\nlateral conv: ",type(lateral_conv),"\n",lateral_conv,"\n---\n")
            # print("\ntop down features: ",type(top_down_features), "\n", top_down_features.shape,"\n----\n")
            # print("\nlateral features: ",type(lateral_features), "\n", lateral_features.shape,"\n----\n")
            prev_features = lateral_features + top_down_features
            if self._fuse_type == "avg":
                prev_features /= 2
            results.insert(0, output_conv(prev_features))

        if self.top_block is not None:
            top_block_in_feature = bottom_up_features.get(self.top_block.in_feature, None)
            if top_block_in_feature is None:
                top_block_in_feature = results[self._out_features.index(self.top_block.in_feature)]
            results.extend(self.top_block(top_block_in_feature))
        assert len(self._out_features) == len(results)
        ret = dict(zip(self._out_features, results))

        p3_p = FTT_get_p3pr(ret['p3'], ret['p4'], self.out_channels, self.norm)
        # p2_p is p3_p upsampled by 2
        p3_p_temp = p3_p
        p3_p = F.interpolate(p3_p, scale_factor=2, mode="nearest")
        # the final lateral_features at the end of the loop is c2_p
        c2_p = lateral_features
        p2_p = p3_p + c2_p 

        ret['p2'] = p2_p       

        # Save files
        import cv2
        import numpy as np
        p2 = ret['p3'].detach().numpy()
        p3 = ret['p4'].detach().numpy()
        p2_p = p2_p.detach().numpy()
        p3_p = p3_p_temp.detach().numpy()

        def save_image(arr, name):
            for i in range(len(arr[0])):
                im = arr[0][i]
                im = np.flip(im, 3)
                im = im / np.amax(im)
                im *= 256
                cv2.imwrite('visual/' + str(i) + '-' + name + '.jpg', im)

        save_image(p2, 'p2')
        save_image(p3, 'p3')
        save_image(p2_p, 'p2_p')
        save_image(p3_p, 'p3_p')
        return ret

    def output_shape(self):
        return {
            name: ShapeSpec(
                channels=self._out_feature_channels[name], stride=self._out_feature_strides[name]
            )
            for name in self._out_features
        }


def _assert_strides_are_log2_contiguous(strides):
    """
    Assert that each stride is 2x times its preceding stride, i.e. "contiguous in log2".
    """
    for i, stride in enumerate(strides[1:], 1):
        assert stride == 2 * strides[i - 1], "Strides {} {} are not log2 contiguous".format(
            stride, strides[i - 1]
        )


class LastLevelMaxPool(nn.Module):
    """
    This module is used in the original FPN to generate a downsampled
    P6 feature from P5.
    """

    def __init__(self):
        super().__init__()
        self.num_levels = 1
        self.in_feature = 'p6' #"p5"

    def forward(self, x):
        return [F.max_pool2d(x, kernel_size=1, stride=2, padding=0)]



@BACKBONE_REGISTRY.register()
def build_resnet_fpn_backbone(cfg, input_shape: ShapeSpec):
    """
    Args:
        cfg: a detectron2 CfgNode

    Returns:
        backbone (Backbone): backbone module, must be a subclass of :class:`Backbone`.
    """
    bottom_up = build_resnet_backbone(cfg, input_shape)
    in_features = cfg.MODEL.FPN.IN_FEATURES
    out_channels = cfg.MODEL.FPN.OUT_CHANNELS
    backbone = FPN(
        bottom_up=bottom_up,
        in_features=in_features,
        out_channels=out_channels,
        norm=cfg.MODEL.FPN.NORM,
        top_block=LastLevelMaxPool(),
        fuse_type=cfg.MODEL.FPN.FUSE_TYPE,
    )
    return backbone