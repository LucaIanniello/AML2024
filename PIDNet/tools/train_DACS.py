# ------------------------------------------------------------------------------
# Modified based on https://github.com/HRNet/HRNet-Semantic-Segmentation
# ------------------------------------------------------------------------------

import argparse
import os
import pprint
import logging
import timeit
import random

import numpy as np
import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
import torch.optim as optim
from tensorboardX import SummaryWriter
from torchvision import transforms

import _init_paths
import models
import datasets
from configs import config
from configs import update_config
from utils.criterion import CrossEntropy, OhemCrossEntropy, BondaryLoss, DiceLoss, FocalLoss
from utils.function_DACS import train, validate
from utils.utils import create_logger, adjust_learning_rate, FullModel


def parse_args():
    parser = argparse.ArgumentParser(description='Train segmentation network')
    parser.add_argument('--cfg', help='experiment configure file name',
                        default="configs/cityscapes/pidnet_small_cityscapes.yaml", type=str)
    parser.add_argument('--seed', type=int, default=304)
    parser.add_argument('opts', help="Modify config options using the command-line",
                        default=None, nargs=argparse.REMAINDER)
    args = parser.parse_args()
    update_config(config, args)
    return args

# Strong augmentations for DACS
strong_augmentations = transforms.Compose([
    transforms.RandomResizedCrop(size=(1024, 1024), scale=(0.5, 2.0)),
    transforms.RandomHorizontalFlip(),
    transforms.ColorJitter(brightness=0.4, contrast=0.4, saturation=0.4, hue=0.2),
    transforms.ToTensor()
])


def main():
    args = parse_args()

    if args.seed > 0:
        print('Seeding with', args.seed)
        random.seed(args.seed)
        torch.manual_seed(args.seed)

    logger, final_output_dir, tb_log_dir = create_logger(config, args.cfg, 'train')
    logger.info(pprint.pformat(args))
    logger.info(config)

    writer_dict = {'writer': SummaryWriter(tb_log_dir), 'train_global_steps': 0, 'valid_global_steps': 0}

    # cudnn related setting
    cudnn.benchmark = config.CUDNN.BENCHMARK
    cudnn.deterministic = config.CUDNN.DETERMINISTIC
    cudnn.enabled = config.CUDNN.ENABLED
    gpus = list(config.GPUS)
    if torch.cuda.device_count() != len(gpus):
        print("The gpu numbers do not match!")
        return 0

    # Prepare model
    imgnet = 'imagenet' in config.MODEL.PRETRAINED
    model = models.pidnet.get_seg_model(config, imgnet_pretrained=imgnet)
   
    # Prepare datasets
    crop_size = (config.TRAIN.IMAGE_SIZE[1], config.TRAIN.IMAGE_SIZE[0])
    source_train_dataset = eval('datasets.' + config.DATASET.SOURCE_DATASET)(
        root=config.DATASET.ROOT,
        list_path=config.DATASET.SOURCE_TRAIN_SET,
        num_classes=config.DATASET.NUM_CLASSES,
        multi_scale=config.TRAIN.MULTI_SCALE,
        flip=config.TRAIN.FLIP,
        ignore_label=config.TRAIN.IGNORE_LABEL,
        base_size=config.TRAIN.BASE_SIZE,
        crop_size=crop_size,
        scale_factor=config.TRAIN.SCALE_FACTOR
    )
    
    source_trainloader = torch.utils.data.DataLoader(
        source_train_dataset,
        batch_size=config.TRAIN.BATCH_SIZE_PER_GPU * len(gpus),
        shuffle=config.TRAIN.SHUFFLE,
        num_workers=config.WORKERS,
        pin_memory=False,
        drop_last=True
    )

    target_train_dataset = eval('datasets.' + config.DATASET.TARGET_DATASET)(
        root=config.DATASET.ROOT,
        list_path=config.DATASET.TARGET_TRAIN_SET,
        num_classes=config.DATASET.NUM_CLASSES,
        multi_scale=config.TRAIN.MULTI_SCALE,
        flip=config.TRAIN.FLIP,
        ignore_label=config.TRAIN.IGNORE_LABEL,
        base_size=config.TRAIN.BASE_SIZE,
        crop_size=(config.TRAIN.IMAGE_SIZE[1], config.TRAIN.IMAGE_SIZE[0]),
        scale_factor=config.TRAIN.SCALE_FACTOR
    )

    target_trainloader = torch.utils.data.DataLoader(
        target_train_dataset,
        batch_size=config.TRAIN.BATCH_SIZE_PER_GPU * len(gpus),
        shuffle=config.TRAIN.SHUFFLE,
        num_workers=config.WORKERS,
        pin_memory=False,
        drop_last=True
    )
    
    # criterion
    if config.LOSS.USE_OHEM:
        sem_criterion = OhemCrossEntropy(ignore_label=config.TRAIN.IGNORE_LABEL,
                                        thres=config.LOSS.OHEMTHRES,
                                        min_kept=config.LOSS.OHEMKEEP,
                                        weight=source_train_dataset.class_weights)
    elif config.LOSS.USE_DICE:
        sem_criterion = DiceLoss(ignore_label=config.TRAIN.IGNORE_LABEL,
                                 eps=1e-6)
    
    elif config.LOSS.USE_FOCAL:
        sem_criterion = FocalLoss(ignore_label=config.TRAIN.IGNORE_LABEL,
                                  weight=source_train_dataset.class_weights)
       
    else:
        sem_criterion = CrossEntropy(ignore_label=config.TRAIN.IGNORE_LABEL,
                                    weight=source_train_dataset.class_weights)

    bd_criterion = BondaryLoss()
    
    model = FullModel(model, sem_criterion, bd_criterion)
    model = nn.DataParallel(model, device_ids=gpus).cuda()

    # optimizer
    if config.TRAIN.OPTIMIZER == 'sgd':
        params_dict = dict(model.named_parameters())
        params = [{'params': list(params_dict.values()), 'lr': config.TRAIN.LR}]

        optimizer = torch.optim.SGD(params,
                                lr=config.TRAIN.LR,
                                momentum=config.TRAIN.MOMENTUM,
                                weight_decay=config.TRAIN.WD,
                                nesterov=config.TRAIN.NESTEROV,
                                )
    elif config.TRAIN.OPTIMIZER == 'adam':
        optimizer = optim.Adam(model.parameters(), lr=config.TRAIN.LR, 
                           weight_decay=config.TRAIN.WD)  
    else:
        raise ValueError('Only Support SGD optimizer')
    
    warmup_epochs = 5  # Number of warm-up epochs
    base_lr = config.TRAIN.LR
    
    start = timeit.default_timer()
    epoch_iters = int(source_train_dataset.__len__() / config.TRAIN.BATCH_SIZE_PER_GPU / len(gpus))
    num_iters = config.TRAIN.END_EPOCH * epoch_iters
    end_epoch = config.TRAIN.END_EPOCH
    real_end = 120+1 if 'camvid' in config.DATASET.TRAIN_SET else end_epoch
    
    if config.TRAIN.SCHEDULER:
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=(config.TRAIN.END_EPOCH - warmup_epochs), eta_min=1e-6
        )
    
    best_mIoU = 0
    flag_rm = config.TRAIN.RESUME
    
     # Training loop modifications in the main script
    for epoch in range(config.TRAIN.BEGIN_EPOCH, config.TRAIN.END_EPOCH):
        
        if config.TRAIN.SCHEDULER:
            if epoch < warmup_epochs:
                adjust_learning_rate(optimizer, epoch, warmup_epochs, base_lr)
            else:
                scheduler.step()
            current_lr = optimizer.param_groups[0]['lr']
        else:
            current_lr = config.TRAIN.LR
            
        # Warm-up phase for learning rate adjustment
        if epoch < warmup_epochs:
            adjust_learning_rate(optimizer, epoch, warmup_epochs, base_lr)

        # Train on source and target domains
        train_metrics = train(
            config=config,
            epoch=epoch,
            num_epoch=config.TRAIN.END_EPOCH,
            epoch_iters=epoch_iters,
            base_lr=current_lr,
            num_iters=num_iters,
            source_loader=source_trainloader,
            target_loader=target_trainloader,
            optimizer=optimizer,
            model=model,
            writer_dict=writer_dict,
            augmentations=strong_augmentations,
            criterion=sem_criterion
        )

        # Log training metrics
        logging.info(f"Epoch {epoch + 1}/{config.TRAIN.END_EPOCH} - "
                    f"Source Loss: {train_metrics['source_loss']:.4f}, "
                    f"Target Loss: {train_metrics['target_loss']:.4f}")


        # Validation and saving checkpoints
        if flag_rm == 1 or (epoch % 5 == 0 and epoch < real_end - 100) or (epoch >= real_end - 100):
            mean_IoU, IoU_array, pixel_acc, mean_acc = validate(config, target_trainloader, model, writer_dict)
        
        if flag_rm == 1:
            flag_rm = 0
            
        # Save checkpoint and best model
        is_best = mean_IoU > best_mIoU
        if is_best:
            best_mIoU = mean_IoU
            torch.save(model.module.state_dict(), os.path.join(final_output_dir, 'best.pt'))

        # Log validation metrics
        msg = f"Epoch [{epoch}], Loss: {train_metrics['total_loss']:.3f}, MeanIoU: {mean_IoU:.4f}, best_mIoU: {best_mIoU:.4f}"
        f"Pixel_Acc: {pixel_acc:.4f}, Mean_Acc: {mean_acc:.4f}"
        logging.info(msg)
        logging.info(f"IoU per class: {IoU_array}")

        torch.save({
            'epoch': epoch + 1,
            'best_mIoU': best_mIoU,
            'state_dict': model.module.state_dict(),
            'optimizer': optimizer.state_dict(),
        }, os.path.join(final_output_dir, 'checkpoint.pth.tar'))
        
    torch.save(model.module.state_dict(),
            os.path.join(final_output_dir, 'final_state.pt'))
    writer_dict['writer'].close()
    end = timeit.default_timer()
    logger.info('Hours: %d' % int((end-start)/3600))
    logger.info('Done')


        
if __name__ == '__main__':
    main()
