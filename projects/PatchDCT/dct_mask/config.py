
def add_dctmask_config(cfg):
    """
    Add config for DCT-Mask.
    """

    # For MaskRCNNDCTHead
    cfg.MODEL.ROI_MASK_HEAD.IN_FEATURES = ["p2", "p3", "p4", "p5"]
    cfg.MODEL.ROI_MASK_HEAD.DCT_VECTOR_DIM = 300
    cfg.MODEL.ROI_MASK_HEAD.MASK_SIZE = 128
    cfg.MODEL.ROI_MASK_HEAD.DCT_LOSS_TYPE = "l1"
    cfg.MODEL.ROI_MASK_HEAD.MASK_LOSS_PARA = 1.0
    cfg.MODEL.ROI_MASK_HEAD.FINE_FEATURES=["p2"]
    cfg.MODEL.ROI_MASK_HEAD.FINE_FEATURES_RESOLUTION = 42
    cfg.MODEL.ROI_MASK_HEAD.MASK_SIZE_ASSEMBLE = 112
    cfg.MODEL.ROI_MASK_HEAD.PATCH_SIZE = 8
    cfg.MODEL.ROI_MASK_HEAD.PATCH_DCT_VECTOR_DIM = 6
    cfg.MODEL.ROI_MASK_HEAD.NUM_STAGE = 2
    cfg.MODEL.ROI_MASK_HEAD.MASK_LOSS_PARA_EACH_STAGE = [0.5,0.5,0.8]
    # cfg.MODEL.ROI_MASK_HEAD.FINE_FEATURES2 = ["p2"]
    # cfg.MODEL.ROI_MASK_HEAD.FINE_FEATURES_RESOLUTION2 = 42
