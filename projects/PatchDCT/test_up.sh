CONFIG=patchdct_r50_1x
CUDA_VISIBLE_DEVICES=1,2,3,4 \
python train_net.py --config-file configs/PatchDCT/${CONFIG}.yaml \
                    --eval-only \
                    --num-gpus 4 --resume \
                    --dist-url "tcp://127.0.0.1:6017" \
                    MODEL.WEIGHTS output/${CONFIG}/model_final.pth \
                    MODEL.ROI_MASK_HEAD.EVAL_GT True \
                    MODEL.META_ARCHITECTURE PatchDCT_UPPER_BOUND_RCNN
