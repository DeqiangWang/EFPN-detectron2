import logging
import numpy as np
import fvcore.nn.weight_init as weight_init
import torch
import torch.nn.functional as F
from torch import nn

from detectron2.layers import Conv2d, ShapeSpec, get_norm

from fpn import FPN

class FTT(FPN):
    """
        in_features should be p2, p3
    """
    def __init__(
        self, fpn, in_features, out_channels, norm = "BN"
    ):
        assert isinstance(fpn, FPN)
        assert in_features == ['p2', 'p3']
        input_shapes = fpn.output_shape()
        assert input_shapes['p3'].channels == input_shapes['p2'].channels
        # in_channels_per_feature = [input_shapes[f].channels for f in in_features]

        # Apply before content extractor to scale up channels from C to 4C
        self.channel_scaler = Conv2d(
            input_shapes['p3'].channels,
            input_shapes['p2'].channels * 4,
            kernel_size=1,
            bias=False,
            norm=''
        )
        self.content_extractor = Extractor(input_shapes['p2'].channels * 4, 3, norm)
        self.texture_extractor = Extractor(input_shapes['p2'].channels * 2, 3, norm)
        self.sub_pixel_conv = SubPixelConv(input_shapes['p2'].channels * 4, 2)

    # x should be a dict mapping from p2 and p3 to their corresponding inputs
    # inputs should have (N, C, H, W) dimensions
    # Returns N p3' tensors
    def forward(self, x):
        assert x['p2'] is not None
        assert x['p3'] is not None
        assert len(x['p2'].shape) == 4
        assert len(x['p3'].shape) == 4

        result = []
        for i in range(x['p3'].shape[0]):
            bottom = x['p3'][i]
            bottom = self.channel_scaler(bottom)
            bottom = self.content_extractor.forward(bottom)
            bottom = self.sub_pixel_conv.forward(bottom)

            # We interpreted "wrap" as concatenating bottom and top
            # so the total channels is doubled after (basically place one on top
            # of the other)
            top = x['p2'][i]
            top = np.concatenate((bottom, top), axis=0)
            top = self.texture_extractor.forward(top)

            # Since top has double the original # of channels, we "cast" bottom
            # to the same shape, then add to get p3'
            bottom = np.concatenate((bottom, bottom), axis=0)
            result.append(bottom + top)

        return result

# Content and Texture Extractor
class Extractor():
    # in and out channels are the same, so for context extractor we'll conv2D
    # before to up it from C to 4C channels
    def __init__(self, num_channels, iterations, norm):
        self.iterations = iterations
        self.conv1 = Conv2d(
            num_channels,
            num_channels,
            kernel_size=1,
            bias=False,
            norm=get_norm(norm, num_channels),
        )
        self.conv2 = Conv2d(
            num_channels,
            num_channels,
            kernel_size=1,
            bias=False,
            norm=get_norm(norm, num_channels),
        )
    
    def _forward_one(self, x):
        out = self.conv1(x)
        out = F.relu_(out)
        out = self.conv2(out)
        return out

    def forward(self, x):
        out = x
        for i in range(self.iterations):
            out = self._forward_one(out)
        return out

class SubPixelConv():
    # in_channels = out_channels * r ^ 2
    def __init__(self, in_channels, r):
        assert in_channels % (r*r) == 0
        self.in_channels = in_channels
        self.out_channels = int(in_channels / (r*r))
        assert self.out_channels * r * r == self.in_channels
        self.r = r
    
    # x.shape should be (in_channels, H, W)
    # output shape is (in_channels / r^2, rH, rW)
    def forward(self, x):
        C, H, W = x.shape
        r = self.r
        assert C == self.in_channels
        output = np.zeros(self.out_channels, r*H, r*W)

        for c in range(self.out_channels):
            for i in range(H):
                for j in range(W):
                    values = x[c*r*r:(c+1)*r*r][i][j]
                    output[c][i:i + r][j:j + r] = np.reshape(values, (r,r))
        return output