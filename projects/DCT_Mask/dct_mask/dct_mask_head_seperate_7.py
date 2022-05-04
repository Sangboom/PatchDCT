

import fvcore.nn.weight_init as weight_init
from typing import List
import torch
from torch import nn
from torch.nn import functional as F

from detectron2.modeling import ROI_MASK_HEAD_REGISTRY
from detectron2.config import configurable
from detectron2.modeling.roi_heads.mask_head import BaseMaskRCNNHead
from detectron2.layers import Conv2d, ShapeSpec, cat, get_norm,ConvTranspose2d
from detectron2.structures import Instances

from .mask_encoding import DctMaskEncoding


@ROI_MASK_HEAD_REGISTRY.register()
class MaskRCNNDCTHead7(BaseMaskRCNNHead):
    """
    A mask head with several conv layers, plus an upsample layer (with `ConvTranspose2d`).
    Predictions are made with a final 1x1 conv layer.
    """

    @configurable
    def __init__(self, input_shape: ShapeSpec, *, num_classes, dct_vector_dim, mask_size,
                 mask_loss_para,
                 dct_loss_type,
                 conv_dims, conv_norm="", **kwargs):
        """
        NOTE: this interface is experimental.

        Args:
            input_shape (ShapeSpec): shape of the input feature
            num_classes (int): the number of classes. 1 if using class agnostic prediction.
            conv_dims (list[int]): a list of N>0 integers representing the output dimensions
                of N-1 conv layers and the last upsample layer.
            conv_norm (str or callable): normalization for the conv layers.
                See :func:`detectron2.layers.get_norm` for supported types.
        """
        super().__init__(**kwargs)
        assert len(conv_dims) >= 1, "conv_dims have to be non-empty!"
        self.dct_vector_dim = dct_vector_dim
        self.dct_vector_dim_coarse = 300
        self.mask_size = mask_size

        self.scale = 14
        self.ratio = 3
        self.b1 = 1
        self.num_tasks = 80
        self.hidden_features = 1024
        self.dct_size = self.mask_size//self.scale
        self.dct_loss_type = dct_loss_type
        self.mask_loss_para = mask_loss_para

        self.dct_encoding_coarse = DctMaskEncoding(vec_dim=self.dct_vector_dim_coarse,mask_size=self.mask_size)
        self.dct_encoding = DctMaskEncoding(vec_dim=self.dct_vector_dim, mask_size=self.dct_size)

        self.conv_norm_relus = []

        cur_channels = input_shape.channels
        for k, conv_dim in enumerate(conv_dims[:-1]):
            conv = Conv2d(
                cur_channels,
                conv_dim,
                kernel_size=3,
                stride=1,
                padding=1,
                bias=not conv_norm,
                norm=get_norm(conv_norm, conv_dim),
                activation=F.relu,
            )
            self.add_module("mask_fcn{}".format(k + 1), conv)
            self.conv_norm_relus.append(conv)
            cur_channels = conv_dim


        self.predictor_coarse = nn.Sequential(
            nn.Linear(self.scale**2*256,self.hidden_features),
            nn.ReLU(),
            nn.Linear(self.hidden_features,self.hidden_features),
            nn.ReLU(),
            nn.Linear(self.hidden_features,self.dct_vector_dim_coarse)
        )

        self.fusion = nn.Sequential(
            Conv2d(cur_channels + 1,
                   conv_dim,
                   kernel_size=1,
                   stride=1,
                   padding=0,
                   bias=not conv_norm,
                   norm=get_norm(conv_norm, conv_dim),
                   activation=F.relu),
            # Conv2d(
            #     cur_channels,
            #     conv_dim,
            #     kernel_size=3,
            #     stride=1,
            #     padding=1,
            #     bias=not conv_norm,
            #     norm=get_norm(conv_norm, conv_dim),
            #     activation=F.relu, )
        )


        self.downsample= nn.Sequential(
            Conv2d(
                    cur_channels,
                    self.hidden_features,
                    kernel_size=self.ratio,
                    stride=self.ratio,
                    padding=0,
                    bias=not conv_norm,
                    norm=get_norm(conv_norm, conv_dim),
                    activation=F.relu, ),
            Conv2d(self.hidden_features,
                   self.hidden_features,
                   kernel_size=3,
                   stride=1,
                   padding=1,
                   bias=not conv_norm,
                   norm=get_norm(conv_norm, conv_dim),
                   activation=F.relu))

        self.predictor1 = Conv2d(self.hidden_features,
                                self.dct_vector_dim*self.num_tasks,
                                kernel_size=1,
                                stride=1,
                                padding=0)
        for layer in self.conv_norm_relus:
            weight_init.c2_msra_fill(layer)


    @classmethod
    def from_config(cls, cfg, input_shape):
        ret = super().from_config(cfg, input_shape)
        conv_dim = cfg.MODEL.ROI_MASK_HEAD.CONV_DIM
        num_conv = cfg.MODEL.ROI_MASK_HEAD.NUM_CONV
        ret.update(
            conv_dims=[conv_dim] * (num_conv + 1),  # +1 for ConvTranspose
            conv_norm=cfg.MODEL.ROI_MASK_HEAD.NORM,
            input_shape=input_shape,
            dct_vector_dim=cfg.MODEL.ROI_MASK_HEAD.DCT_VECTOR_DIM,

            mask_loss_para=cfg.MODEL.ROI_MASK_HEAD.MASK_LOSS_PARA,
            mask_size=cfg.MODEL.ROI_MASK_HEAD.MASK_SIZE,
            dct_loss_type=cfg.MODEL.ROI_MASK_HEAD.DCT_LOSS_TYPE,

        )

        if cfg.MODEL.ROI_MASK_HEAD.CLS_AGNOSTIC_MASK:
            ret["num_classes"] = 1
        else:
            ret["num_classes"] = cfg.MODEL.ROI_HEADS.NUM_CLASSES
        return ret

    def layers(self, x,fine_mask_features,instances):
        for layer in self.conv_norm_relus:
            x = layer(x)

        fg = self.predictor_coarse(x.flatten(start_dim=1))


        masks = self.dct_encoding_coarse.decode(fg).real
        masks = masks.reshape(-1,1,self.mask_size,self.mask_size)

        masks = F.interpolate(masks,(self.scale*self.ratio,self.scale*self.ratio))
        x = self.fusion(torch.cat((masks,fine_mask_features), dim=1))
        x= self.downsample(x)

        x = self.predictor1(x)

        x = x.permute(0,2,3,1).reshape(-1,self.num_tasks,self.dct_vector_dim)
        return x,fg

    def forward(self, x, fine_mask_features,instances: List[Instances]):
        """
        Args:
            x: input region feature(s) provided by :class:`ROIHeads`.
            instances (list[Instances]): contains the boxes & labels corresponding
                to the input features.
                Exact format is up to its caller to decide.
                Typically, this is the foreground instances in training, with
                "proposal_boxes" field and other gt annotations.
                In inference, it contains boxes that are already predicted.

        Returns:
            A dict of losses in training. The predicted "instances" in inference.
        """
        x,fg = self.layers(x,fine_mask_features,instances)
        if self.training:
            return {"loss_mask": self.mask_rcnn_dct_loss(x,fg,instances, self.vis_period)}
        else:
            pred_instances = self.mask_rcnn_dct_inference(x,fg,instances)
            return pred_instances

    def mask_rcnn_dct_loss(self, pred_mask_logits,fg,instances, vis_period=0):
        """
        Compute the mask prediction loss defined in the Mask R-CNN paper.

        Args:
            pred_mask_logits (Tensor): [B, D]. D is dct-dim. [B, D]. DCT_Vector.
            
            instances (list[Instances]): A list of N Instances, where N is the number of images
                in the batch. These instances are in 1:1
                correspondence with the pred_mask_logits. The ground-truth labels (class, box, mask,
                ...) associated with each instance are stored in fields.
            vis_period (int): the period (in steps) to dump visualization.

        Returns:
            mask_loss (Tensor): A scalar tensor containing the loss.
        """
        gt_masks,gt_classes,gt_masks_coarse= self.get_gt_mask(instances,pred_mask_logits)
        num_instance = gt_classes.size()[0]
        num_patch = gt_masks.size()[0]
        gt_classes = gt_classes.reshape(-1,1).expand(num_instance,self.scale**2).reshape(-1)
        pred_mask_logits = pred_mask_logits[torch.arange(num_patch),gt_classes]

        index = (gt_masks[:,0]>self.b1)&(gt_masks[:,0]<self.dct_size-self.b1)
        pred_mask_logits = pred_mask_logits[index,:]
        gt_masks = gt_masks[index,:]

        if self.dct_loss_type == "l1":
            mask_loss_1 = F.l1_loss(pred_mask_logits, gt_masks)
            mask_loss_2 = F.l1_loss(fg,gt_masks_coarse)
            mask_loss = mask_loss_1 + mask_loss_2
            mask_loss = self.mask_loss_para * mask_loss
            
        elif self.dct_loss_type == "sl1":
            num_instance = gt_masks.size()[0]
            mask_loss = F.smooth_l1_loss(pred_mask_logits, gt_masks, reduction="none")
            mask_loss = self.mask_loss_para * mask_loss / num_instance
            mask_loss = torch.sum(mask_loss)
        elif self.dct_loss_type == "l2":
            num_instance = gt_masks.size()[0]
            mask_loss = F.mse_loss(pred_mask_logits, gt_masks, reduction="none")
            mask_loss = self.mask_loss_para * mask_loss / num_instance
            mask_loss = torch.sum(mask_loss)
        else:
            raise ValueError("Loss Type Only Support : l1, l2; yours: {}".format(self.dct_loss_type))

        return mask_loss

    def mask_rcnn_dct_inference(self,pred_mask_logits,fg,pred_instances):
        """
        Convert pred_mask_logits to estimated foreground probability masks while also
        extracting only the masks for the predicted classes in pred_instances. For each
        predicted box, the mask of the same class is attached to the instance by adding a
        new "pred_masks" field to pred_instances.

        Args:
            pred_mask_logits (Tensor): A tensor of shape (B, C, Hmask, Wmask) or (B, 1, Hmask, Wmask)
                for class-specific or class-agnostic, where B is the total number of predicted masks
                in all images, C is the number of foreground classes, and Hmask, Wmask are the height
                and width of the mask predictions. The values are logits.
            pred_instances (list[Instances]): A list of N Instances, where N is the number of images
                in the batch. Each Instances must have field "pred_classes".

        Returns:
            None. pred_instances will contain an extra "pred_masks" field storing a mask of size (Hmask,
                Wmask) for predicted class. Note that the masks are returned as a soft (non-quantized)
                masks the resolution predicted by the network; post-processing steps, such as resizing
                the predicted masks to the original image resolution and/or binarizing them, is left
                to the caller.
        """

        num_patch = pred_mask_logits.shape[0]
        pred_classes = pred_instances[0].pred_classes
        num_masks = pred_classes.shape[0]
        pred_classes=pred_classes.reshape(-1, 1).expand(num_masks, self.scale ** 2).reshape(-1)
        device = pred_mask_logits.device

        indices = torch.arange(num_patch)
        pred_mask_logits = pred_mask_logits[indices,pred_classes]


        if num_masks == 0:
            pred_instances[0].pred_masks = torch.empty([0, 1, self.mask_size, self.mask_size]).to(device)
            return pred_instances
        else:
            with torch.no_grad():
                fg = self.dct_encoding_coarse.decode(fg).real
                fg = fg[None,None,:,:]
                fg[fg<0.5] = 0
                fg[fg>=0.5] = 1
                index = F.adaptive_avg_pool2d(fg,self.scale).reshape(-1)


                pred_mask_logits[index<=self.b1,::] = 0
                pred_mask_logits[index>=self.dct_size-self.b1,::] = 0
                pred_mask_logits[index>=self.dct_size-self.b1,0] = self.dct_size

                pred_mask_rc = self.dct_encoding.decode(pred_mask_logits)
                pred_mask_rc = pred_mask_rc.reshape(-1, self.scale, self.scale, self.dct_size, self.dct_size)
                pred_mask_rc = pred_mask_rc.permute(0, 1, 2, 4, 3)
                pred_mask_rc = pred_mask_rc.reshape(-1, self.scale, self.mask_size, self.dct_size)
                pred_mask_rc = pred_mask_rc.permute(0, 1, 3, 2)

                pred_mask_rc = pred_mask_rc.reshape(-1, self.mask_size, self.mask_size)


            pred_mask_rc = pred_mask_rc[:, None, :, :]
            pred_instances[0].pred_masks = pred_mask_rc
            return pred_instances

    def get_gt_coarse_mask(self,instances):
        gt_masks = []
        for instances_per_image in instances:
            if len(instances_per_image) == 0:
                continue
            gt_masks_per_image = instances_per_image.gt_masks.crop_and_resize(
                instances_per_image.proposal_boxes.tensor, self.mask_size)
            gt_masks.append(gt_masks_per_image.reshape(-1,1,self.mask_size,self.mask_size))
        gt_masks = cat(gt_masks,dim=0).to(dtype = torch.float32)
        return gt_masks


    def get_gt_mask(self,instances,pred_mask_logits):
        gt_masks = []
        gt_masks_coarse = []
        gt_classes = []
        for instances_per_image in instances:

            if len(instances_per_image) == 0:
                continue

            gt_masks_per_image = instances_per_image.gt_masks.crop_and_resize(
                instances_per_image.proposal_boxes.tensor, self.mask_size)

            gt_masks_vector = self.dct_encoding_coarse.encode(gt_masks_per_image)
            gt_masks_coarse.append(gt_masks_vector)

            gt_masks_per_image = gt_masks_per_image.reshape(-1, self.scale, self.dct_size, self.mask_size)
            gt_masks_per_image = gt_masks_per_image.permute(0, 1, 3, 2)
            gt_masks_per_image = gt_masks_per_image.reshape(-1, self.scale, self.scale, self.dct_size, self.dct_size)
            gt_masks_per_image = gt_masks_per_image.permute(0, 1, 2, 4, 3)
            gt_masks_per_image = gt_masks_per_image.reshape(-1, self.dct_size, self.dct_size)

            gt_masks.append(gt_masks_per_image)

            gt_classes_per_image = instances_per_image.gt_classes.to(dtype=torch.int64)
            gt_classes.append(gt_classes_per_image)

        if len(gt_masks) == 0:
            return pred_mask_logits.sum() * 0

        gt_masks_coarse = cat(gt_masks_coarse,dim=0)

        gt_masks = cat(gt_masks, dim=0)
        gt_masks = self.dct_encoding.encode(gt_masks)
        gt_masks = gt_masks.to(dtype=torch.float32)
        gt_classes = cat(gt_classes, dim=0)
        return gt_masks,gt_classes,gt_masks_coarse


    def get_gt_mask_inference(self,instances,pred_mask_logits):
        gt_masks = []

        for instances_per_image in instances:
            if len(instances_per_image) == 0:
                continue
            if instances_per_image.has("gt_masks"):
                gt_masks_per_image = instances_per_image.gt_masks.crop_and_resize(
                    instances_per_image.pred_boxes.tensor, self.mask_size)
            else:
                #print("gt_mask is empty")
                shape = instances_per_image.pred_boxes.tensor.shape[0]
                device = instances_per_image.pred_boxes.tensor.device
                gt_masks_per_image = torch.zeros((shape,self.mask_size,self.mask_size),dtype=torch.bool).to(device)



            gt_masks_per_image = gt_masks_per_image.reshape(-1, self.scale, self.dct_size, self.mask_size)
            gt_masks_per_image = gt_masks_per_image.permute(0, 1, 3, 2)
            gt_masks_per_image = gt_masks_per_image.reshape(-1, self.scale, self.scale, self.dct_size, self.dct_size)
            gt_masks_per_image = gt_masks_per_image.permute(0, 1, 2, 4, 3)

            gt_masks_per_image = gt_masks_per_image.reshape(-1, self.dct_size, self.dct_size)
            gt_masks_vector = self.dct_encoding.encode(gt_masks_per_image)
            gt_masks.append((gt_masks_vector))

        if len(gt_masks) == 0:
            return pred_mask_logits.sum() * 0

        gt_masks = cat(gt_masks, dim=0)

        gt_masks = gt_masks.to(dtype=torch.float32)
        return gt_masks
