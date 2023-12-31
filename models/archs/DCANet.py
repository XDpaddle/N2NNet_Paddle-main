import paddle
import paddle.nn as nn
import paddle.nn.functional as F


##---------- Spatial Attention ----------
class BasicConv(nn.Layer):
    def __init__(self, in_planes, out_planes, kernel_size, stride=1, padding=0, dilation=1, groups=1, relu=True, bn=False, bias=False):
        super(BasicConv, self).__init__()
        self.out_channels = out_planes
        self.conv = nn.Conv2D(in_planes, out_planes, kernel_size=kernel_size, stride=stride, padding=padding, dilation=dilation, groups=groups, bias_attr=bias)
        self.bn = nn.BatchNorm2D(out_planes, eps=1e-5, momentum=0.01, affine=True) if bn else None
        self.relu = nn.ReLU() if relu else None

    def forward(self, x):
        x = self.conv(x)
        if self.bn is not None:
            x = self.bn(x)
        if self.relu is not None:
            x = self.relu(x)
        return x


class ChannelPool(nn.Layer):
    def __init__(self):
        super(ChannelPool, self).__init__()

    def forward(self, x):
        return paddle.concat((paddle.max(x, 1).unsqueeze(1), paddle.mean(x, 1).unsqueeze(1)), axis=1)


class spatial_attn_layer(nn.Layer):
    def __init__(self):
        super(spatial_attn_layer, self).__init__()
        kernel_size = 5
        self.compress = ChannelPool()
        self.spatial = BasicConv(2, 1, kernel_size, stride=1, padding=(kernel_size - 1) // 2, relu=False)

    def forward(self, x):
        # import pdb;pdb.set_trace()
        x_compress = self.compress(x)
        x_out = self.spatial(x_compress)
        scale = F.sigmoid(x_out)  # broadcasting
        return x * scale


## Channel Attention Layer
class CALayer(nn.Layer):
    def __init__(self, nc, reduction=3, bias=False):
        super(CALayer, self).__init__()
        # global average pooling: feature --> point
        self.avg_pool = nn.AdaptiveAvgPool2D(1)
        # feature channel downscale and upscale --> channel weight
        self.conv_du = nn.Sequential(
                nn.Conv2D(nc, nc // reduction, kernel_size=1, padding=0, bias_attr=bias),
                nn.ReLU(),
                nn.Conv2D(nc // reduction, nc, kernel_size=1, padding=0, bias_attr=bias),
                nn.Sigmoid()
        )

    def forward(self, x):
        y = self.avg_pool(x)
        y = self.conv_du(y)
        return x * y


##---------- Dual Attention Unit (DAU) ----------
class DAU(nn.Layer):
    def __init__(self, nc, bias=False):
        super(DAU, self).__init__()
        kernel_size = 3
        reduction = 8
        layer = []
        layer.append(nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias))
        layer.append(nn.PReLU())
        layer.append(nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias))
        self.body = nn.Sequential(*layer)
        ## Spatial Attention
        self.SA = spatial_attn_layer()

        ## Channel Attention
        self.CA = CALayer(nc, reduction, bias=bias)

        self.conv1x1 = nn.Conv2D(nc * 2, nc, kernel_size=1, bias_attr=bias)

    def forward(self, x):
        res = self.body(x)
        sa_branch = self.SA(res)
        ca_branch = self.CA(res)
        res = paddle.concat([sa_branch, ca_branch], 1)
        res = self.conv1x1(res)
        res += x
        return res


class FCN(nn.Layer):
    def __init__(self, in_nc=3, out_nc=3, nc=64):
        super(FCN, self).__init__()
        kernel_size = 3
        layers = []
        layers.append(nn.Conv2D(in_nc, nc, kernel_size=kernel_size, padding=1))
        layers.append(nn.ReLU())
        for _ in range(5):
            layers.append(nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1))
            layers.append(nn.BatchNorm2D(nc))
            layers.append(nn.ReLU())
        layers.append(nn.Conv2D(nc, out_nc, kernel_size=kernel_size, padding=1))
        self.fcn = nn.Sequential(*layers)
        self.tanh_mapping = nn.Tanh()

    def forward(self, x):
        noise_level = self.fcn(x)
        noise_level = self.tanh_mapping(noise_level)
        return noise_level


class Up(nn.Layer):
    def __init__(self):
        super().__init__()
        self.up = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True)

    def forward(self, x1, x):
        x2 = self.up(x1)

        diffY = x.shape[2] - x2.shape[2]
        diffX = x.shape[3] - x2.shape[3]
        x3 = F.pad(x2, [diffX // 2, diffX - diffX // 2, diffY // 2, diffY - diffY // 2])
        return x3


class DCANet(nn.Layer):
    def __init__(self, in_nc=3, out_nc=3, nc=64, bias=True):
        super(DCANet, self).__init__()
        kernel_size = 3
        self.fcn = FCN(in_nc=in_nc, out_nc=out_nc, nc=nc)
        self.dau = DAU(nc, bias)

        self.conv_head = nn.Conv2D(2 * in_nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)

        self.conv1 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn1 = nn.BatchNorm2D(nc)
        self.relu1 = nn.ReLU()

        self.conv2 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn2 = nn.BatchNorm2D(nc)
        self.relu2 = nn.ReLU()

        self.conv3 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn3 = nn.BatchNorm2D(nc)
        self.relu3 = nn.ReLU()

        self.conv4 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn4 = nn.BatchNorm2D(nc)
        self.relu4 = nn.ReLU()

        self.mp1 = nn.MaxPool2D(2)

        self.conv5 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn5 = nn.BatchNorm2D(nc)
        self.relu5 = nn.ReLU()

        self.conv6 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn6 = nn.BatchNorm2D(nc)
        self.relu6 = nn.ReLU()

        self.conv7 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn7 = nn.BatchNorm2D(nc)
        self.relu7 = nn.ReLU()

        self.mp2 = nn.MaxPool2D(2)

        self.conv8 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn8 = nn.BatchNorm2D(nc)
        self.relu8 = nn.ReLU()

        self.conv9 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn9 = nn.BatchNorm2D(nc)
        self.relu9 = nn.ReLU()

        # upsample

        self.conv10 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn10 = nn.BatchNorm2D(nc)
        self.relu10 = nn.ReLU()

        self.conv11 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn11 = nn.BatchNorm2D(nc)
        self.relu11 = nn.ReLU()

        self.conv12 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn12 = nn.BatchNorm2D(nc)
        self.relu12 = nn.ReLU()

        # upsample

        self.conv13 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn13 = nn.BatchNorm2D(nc)
        self.relu13 = nn.ReLU()

        self.conv14 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn14 = nn.BatchNorm2D(nc)
        self.relu14 = nn.ReLU()

        self.conv15 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, bias_attr=bias)
        self.bn15 = nn.BatchNorm2D(nc)
        self.relu15 = nn.ReLU()

        self.conv16 = nn.Conv2D(nc, out_nc, kernel_size=kernel_size, padding=1, bias_attr=bias)

        self.m_dilatedconv1 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, dilation=1, bias_attr=bias)
        self.m_bn1 = nn.BatchNorm2D(nc)
        self.m_relu1 = nn.ReLU()

        self.m_dilatedconv2 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=2, dilation=2, bias_attr=bias)
        self.m_bn2 = nn.BatchNorm2D(nc)
        self.m_relu2 = nn.ReLU()

        self.m_dilatedconv3 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=3, dilation=3, bias_attr=bias)
        self.m_bn3 = nn.BatchNorm2D(nc)
        self.m_relu3 = nn.ReLU()

        self.m_dilatedconv4 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=4, dilation=4, bias_attr=bias)
        self.m_bn4 = nn.BatchNorm2D(nc)
        self.m_relu4 = nn.ReLU()

        self.m_dilatedconv5 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=5, dilation=5, bias_attr=bias)
        self.m_bn5 = nn.BatchNorm2D(nc)
        self.m_relu5 = nn.ReLU()

        self.m_dilatedconv6 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=6, dilation=6, bias_attr=bias)
        self.m_bn6 = nn.BatchNorm2D(nc)
        self.m_relu6 = nn.ReLU()

        self.m_dilatedconv7 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=7, dilation=7, bias_attr=bias)
        self.m_bn7 = nn.BatchNorm2D(nc)
        self.m_relu7 = nn.ReLU()

        self.m_dilatedconv8 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=8, dilation=8, bias_attr=bias)
        self.m_bn8 = nn.BatchNorm2D(nc)
        self.m_relu8 = nn.ReLU()

        self.m_dilatedconv7_1 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=7, dilation=7, bias_attr=bias)
        self.m_bn7_1 = nn.BatchNorm2D(nc)
        self.m_relu7_1 = nn.ReLU()

        self.m_dilatedconv6_1 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=6, dilation=6, bias_attr=bias)
        self.m_bn6_1 = nn.BatchNorm2D(nc)
        self.m_relu6_1 = nn.ReLU()

        self.m_dilatedconv5_1 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=5, dilation=5, bias_attr=bias)
        self.m_bn5_1 = nn.BatchNorm2D(nc)
        self.m_relu5_1 = nn.ReLU()

        self.m_dilatedconv4_1 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=4, dilation=4, bias_attr=bias)
        self.m_bn4_1 = nn.BatchNorm2D(nc)
        self.m_relu4_1 = nn.ReLU()

        self.m_dilatedconv3_1 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=3, dilation=3, bias_attr=bias)
        self.m_bn3_1 = nn.BatchNorm2D(nc)
        self.m_relu3_1 = nn.ReLU()

        self.m_dilatedconv2_1 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=2, dilation=2, bias_attr=bias)
        self.m_bn2_1 = nn.BatchNorm2D(nc)
        self.m_relu2_1 = nn.ReLU()

        self.m_dilatedconv1_1 = nn.Conv2D(nc, nc, kernel_size=kernel_size, padding=1, dilation=1, bias_attr=bias)
        self.m_bn1_1 = nn.BatchNorm2D(nc)
        self.m_relu1_1 = nn.ReLU()

        self.m_dilatedconv = nn.Conv2D(nc, out_nc, kernel_size=kernel_size, padding=1, dilation=1, bias_attr=bias)

        self.conv_tail = nn.Conv2D(2*out_nc, out_nc, kernel_size=kernel_size, padding=1, bias_attr=bias)

        self.up = Up()

    def forward(self, x0):
        noise_level = self.fcn(x0)
        x0_0 = paddle.concat((x0, noise_level), 1)

        x0_1 = self.conv_head(x0_0)
        x0_2 = self.dau(x0_1)

        x1 = self.conv1(x0_2)
        x1 = self.bn1(x1)
        x1 = self.relu1(x1)
        x2 = self.conv2(x1)
        x2 = self.bn2(x2)
        x2 = self.relu2(x2)
        x3 = self.conv3(x2)
        x3 = self.bn3(x3)
        x3 = self.relu3(x3)
        x4 = self.conv4(x3)
        x4 = self.bn4(x4)
        x4 = self.relu4(x4)

        x4_1 = self.mp1(x4)

        x5 = self.conv5(x4_1)
        x5 = self.bn5(x5)
        x5 = self.relu5(x5)
        x6 = self.conv6(x5)
        x6 = self.bn6(x6)
        x6 = self.relu6(x6)
        x7 = self.conv7(x6)
        x7 = self.bn7(x7)
        x7 = self.relu7(x7)

        x7_1 = self.mp2(x7)

        x8 = self.conv8(x7_1)
        x8 = self.bn8(x8)
        x8 = self.relu8(x8)
        x9 = self.conv9(x8)
        x9 = self.bn9(x9)
        x9 = self.relu9(x9)

        x9_1 = self.up(x9, x7)

        x10 = self.conv10(x9_1+x7)
        x10 = self.bn10(x10)
        x10 = self.relu10(x10)
        x11= self.conv11(x10)
        x11 = self.bn11(x11)
        x11 = self.relu11(x11)
        x12 = self.conv12(x11)
        x12 = self.bn12(x12)
        x12 = self.relu12(x12)

        x12_1 = self.up(x12, x4)

        x13 = self.conv13(x12_1+x4)
        x13 = self.bn13(x13)
        x13 = self.relu13(x13)
        x14 = self.conv14(x13)
        x14 = self.bn14(x14)
        x14 = self.relu14(x14)
        x15 = self.conv15(x14)
        x15 = self.bn15(x15)
        x15 = self.relu15(x15)
        x = self.conv16(x15)

        X = x + x0

        y1 = self.m_dilatedconv1(x0_2)
        y1_1 = self.m_bn1(y1)
        y1_1 = self.m_relu1(y1_1)

        y2 = self.m_dilatedconv2(y1_1)
        y2_1 = self.m_bn2(y2)
        y2_1 = self.m_relu2(y2_1)

        y3 = self.m_dilatedconv3(y2_1)
        y3_1 = self.m_bn3(y3)
        y3_1 = self.m_relu3(y3_1)

        y4 = self.m_dilatedconv4(y3_1)
        y4_1 = self.m_bn4(y4)
        y4_1 = self.m_relu4(y4_1)

        y5 = self.m_dilatedconv5(y4_1)
        y5_1 = self.m_bn5(y5)
        y5_1 = self.m_relu5(y5_1)

        y6 = self.m_dilatedconv6(y5_1)
        y6_1 = self.m_bn6(y6)
        y6_1 = self.m_relu6(y6_1)

        y7 = self.m_dilatedconv7(y6_1)
        y7_1 = self.m_bn7(y7)
        y7_1 = self.m_relu7(y7_1)

        y8 = self.m_dilatedconv8(y7_1)
        y8_1 = self.m_bn8(y8)
        y8_1 = self.m_relu8(y8_1)

        y9 = self.m_dilatedconv7_1(y8_1)
        y9 = self.m_bn7_1(y9)
        y9 = self.m_relu7_1(y9)

        y10 = self.m_dilatedconv6_1(y9+y7)
        y10 = self.m_bn6_1(y10)
        y10 = self.m_relu6_1(y10)

        y11 = self.m_dilatedconv5_1(y10+y6)
        y11 = self.m_bn5_1(y11)
        y11 = self.m_relu5_1(y11)

        y12 = self.m_dilatedconv4_1(y11+y5)
        y12 = self.m_bn4_1(y12)
        y12 = self.m_relu4_1(y12)

        y13 = self.m_dilatedconv3_1(y12+y4)
        y13 = self.m_bn3_1(y13)
        y13 = self.m_relu3_1(y13)

        y14 = self.m_dilatedconv2_1(y13+y3)
        y14 = self.m_bn2_1(y14)
        y14 = self.m_relu2_1(y14)

        y15 = self.m_dilatedconv1_1(y14+y2)
        y15 = self.m_bn1_1(y15)
        y15 = self.m_relu1_1(y15)

        y = self.m_dilatedconv(y15+y1)

        Y = y + x0

        z0 = paddle.concat([X, Y], 1)
        z = self.conv_tail(z0)
        Z = z + x0
        return Z