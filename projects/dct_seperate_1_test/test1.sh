CUDA_VISIBLE_DEVICES=0 \
python train_net.py --config-file configs/DCT/test.yaml\
                    --eval-only \
                    --num-gpus 1 --resume \
                    --dist-url "tcp://127.0.0.1:6017"
