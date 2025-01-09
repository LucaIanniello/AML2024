# ------------------------------------------------------------------------------
# Modified based on https://github.com/HRNet/HRNet-Semantic-Segmentation
# ------------------------------------------------------------------------------

import logging
import os
import time

import numpy as np
from tqdm import tqdm

import torch
from torch.nn import functional as F

from utils.utils import AverageMeter
from utils.utils import get_confusion_matrix
from utils.utils import adjust_learning_rate
from tools.train_DACS import generate_pseudo_labels
from utils.criterion import CrossEntropy, OhemCrossEntropy

from kornia.augmentation import RandomMixUp


def train(config, epoch, num_epoch, epoch_iters, base_lr,
          num_iters, source_loader, target_loader, optimizer, model, writer_dict, augmentations):
    model.train()

    batch_time = AverageMeter()
    ave_loss = AverageMeter()
    ave_acc = AverageMeter()
    avg_sem_loss = AverageMeter()
    avg_bce_loss = AverageMeter()
    tic = time.time()
    cur_iters = epoch * epoch_iters
    writer = writer_dict['writer']
    global_steps = writer_dict['train_global_steps']

    mixup_fn = RandomMixUp(p=1.0, lambda_val=(0.5, 0.5))  # MixUp augmentation for source and target images

    source_loss_total = 0
    target_loss_total = 0

    for i_iter, (source_data, target_data) in enumerate(zip(source_loader, target_loader), 0):
        # --- Load source domain data ---
        source_images, source_labels = source_data
        source_images, source_labels = source_images.cuda(), source_labels.long().cuda()

        # --- Load target domain data ---
        target_images, _ = target_data  # Ignore target labels
        target_images = target_images.cuda()

        # --- Apply augmentations to target domain ---
        augmented_target_images = augmentations(target_images)

        # --- Generate pseudo-labels for target domain ---
        with torch.no_grad():
            target_logits = model(augmented_target_images)
            pseudo_labels = torch.argmax(target_logits, dim=1)
            confidence_mask = torch.max(F.softmax(target_logits, dim=1), dim=1).values > config.TRAIN.CONFIDENCE_THRESHOLD
            pseudo_labels = pseudo_labels[confidence_mask]
            confident_images = augmented_target_images[confidence_mask]

        # --- Compute source loss ---
        source_logits = model(source_images)
        source_loss, _, source_acc, _ = source_logits  # Assuming model returns (loss, _, accuracy, loss_list)
        source_loss_total += source_loss.item()
        
        # --- Compute target loss (only for confident pseudo-labels) ---
        if confident_images.size(0) > 0:  # Ensure valid pseudo-labels exist
            confident_logits = model(confident_images)
            target_loss, _, target_acc, _ = confident_logits  # Assuming model returns (loss, _, accuracy, loss_list)
        else:
            target_loss = torch.tensor(0.0, requires_grad=True).cuda()
            target_acc = torch.tensor(0.0, device=source_acc.device)
        target_loss_total += target_loss.item()

        # --- Apply MixUp augmentation between source and target images ---
        mixed_images, mixed_labels = mixup_fn(source_images, source_labels, confident_images, pseudo_labels)
        mixed_logits = model(mixed_images)
        mixup_loss, _, mixup_acc, _ = mixed_logits  # Assuming model returns (loss, _, accuracy, loss_list)

        # --- Compute total loss ---
        total_batch_loss = (
            config.TRAIN.SOURCE_LOSS_WEIGHT * source_loss +
            config.TRAIN.TARGET_LOSS_WEIGHT * target_loss +
            config.TRAIN.MIXUP_LOSS_WEIGHT * mixup_loss
        )

        # --- Measure average accuracy ---
        acc = (source_acc + target_acc + mixup_acc) / 3  # Averaging source, target, and mixup accuracy

        # --- Measure elapsed time ---
        batch_time.update(time.time() - tic)
        tic = time.time()

        # --- Update average loss ---
        ave_loss.update(total_batch_loss.item())
        ave_acc.update(acc.item())
        avg_sem_loss.update(source_loss.item())
        avg_bce_loss.update(target_loss.item())

        # --- Update learning rate ---
        lr = adjust_learning_rate(optimizer, base_lr, num_iters, i_iter + cur_iters)

        # --- Log training progress ---
        if i_iter % config.PRINT_FREQ == 0:
            msg = 'Epoch: [{}/{}] Iter:[{}/{}], Time: {:.2f}, ' \
                  'lr: {}, Loss: {:.6f}, Acc:{:.6f}, Source Loss: {:.6f}, Target Loss: {:.6f}, MixUp Loss: {:.6f}' .format(
                      epoch, num_epoch, i_iter, epoch_iters,
                      batch_time.average(), [x['lr'] for x in optimizer.param_groups], ave_loss.average(),
                      ave_acc.average(), avg_sem_loss.average(), avg_bce_loss.average(), mixup_loss.item())
            logging.info(msg)

        # --- Backpropagation and optimization ---
        optimizer.zero_grad()
        total_batch_loss.backward()
        optimizer.step()

    # --- Update Tensorboard ---
    writer.add_scalar('train_loss', ave_loss.average(), global_steps)
    writer.add_scalar('train_acc', ave_acc.average(), global_steps)
    writer_dict['train_global_steps'] = global_steps + 1

    # Return training metrics as a dictionary
    train_metrics = {
        'source_loss': source_loss_total / epoch_iters,
        'target_loss': target_loss_total / epoch_iters,
        'total_loss': ave_loss.average(),
        'accuracy': ave_acc.average()
    }
    
    return train_metrics



def validate(config, testloader, model, writer_dict):
    model.eval()
    ave_loss = AverageMeter()
    nums = config.MODEL.NUM_OUTPUTS
    confusion_matrix = np.zeros(
        (config.DATASET.NUM_CLASSES, config.DATASET.NUM_CLASSES, nums))
    
    with torch.no_grad():
        for idx, batch in enumerate(testloader):
            # Unpack batch and move tensors to GPU
            image, label, bd_gts, _, _ = batch
            size = label.size()
            image = image.cuda(non_blocking=True)
            label = label.long().cuda(non_blocking=True)
            bd_gts = bd_gts.float().cuda(non_blocking=True)

            # Forward pass
            losses, pred, pseudo_label, confidence_mask = model(image, label, bd_gts)

            if not isinstance(pred, (list, tuple)):
                pred = [pred]
            
            for i, x in enumerate(pred):
                # Upsample prediction to match label size
                x = F.interpolate(
                    input=x, size=size[-2:],
                    mode='bilinear', align_corners=config.MODEL.ALIGN_CORNERS
                )

                # Update confusion matrix
                confusion_matrix[..., i] += get_confusion_matrix(
                    label,
                    x,
                    size,
                    config.DATASET.NUM_CLASSES,
                    config.TRAIN.IGNORE_LABEL
                )
            
            # Compute loss and update the average
            loss = losses.mean().item()
            ave_loss.update(loss)

            if idx % 10 == 0 or idx == len(testloader) - 1:
                logging.info(f'Validation Progress: {idx + 1}/{len(testloader)} batches processed.')

    # Compute IoU metrics for each output
    IoU_arrays = []
    mean_IoUs = []
    for i in range(nums):
        pos = confusion_matrix[..., i].sum(1)
        res = confusion_matrix[..., i].sum(0)
        tp = np.diag(confusion_matrix[..., i])
        IoU_array = tp / np.maximum(1.0, pos + res - tp)
        mean_IoU = IoU_array.mean()

        IoU_arrays.append(IoU_array)
        mean_IoUs.append(mean_IoU)

        logging.info(f'Output {i}: IoU per class: {IoU_array}, Mean IoU: {mean_IoU}')

    # Log validation results to TensorBoard
    writer = writer_dict['writer']
    global_steps = writer_dict['valid_global_steps']
    writer.add_scalar('valid_loss', ave_loss.average(), global_steps)

    for i, mean_IoU in enumerate(mean_IoUs):
        writer.add_scalar(f'valid_mIoU_output_{i}', mean_IoU, global_steps)
        writer.add_scalars(
            f'valid_IoU_output_{i}',
            {f'class_{c}': IoU for c, IoU in enumerate(IoU_arrays[i])},
            global_steps
        )

    writer_dict['valid_global_steps'] = global_steps + 1

    # Return validation metrics
    return ave_loss.average(), mean_IoUs, IoU_arrays


def testval(config, test_dataset, testloader, model,
            sv_dir='./', sv_pred=False):
    model.eval()
    confusion_matrix = np.zeros((config.DATASET.NUM_CLASSES, config.DATASET.NUM_CLASSES))
    with torch.no_grad():
        for index, batch in enumerate(tqdm(testloader)):
            image, label, _, _, name = batch
            size = label.size()
            pred = test_dataset.single_scale_inference(config, model, image.cuda())

            if pred.size()[-2] != size[-2] or pred.size()[-1] != size[-1]:
                pred = F.interpolate(
                    pred, size[-2:],
                    mode='bilinear', align_corners=config.MODEL.ALIGN_CORNERS
                )
            
            confusion_matrix += get_confusion_matrix(
                label,
                pred,
                size,
                config.DATASET.NUM_CLASSES,
                config.TRAIN.IGNORE_LABEL)

            if sv_pred:
                sv_path = os.path.join(sv_dir, 'val_results')
                if not os.path.exists(sv_path):
                    os.mkdir(sv_path)
                test_dataset.save_pred(pred, sv_path, name)

            if index % 100 == 0:
                logging.info('processing: %d images' % index)
                pos = confusion_matrix.sum(1)
                res = confusion_matrix.sum(0)
                tp = np.diag(confusion_matrix)
                IoU_array = (tp / np.maximum(1.0, pos + res - tp))
                mean_IoU = IoU_array.mean()
                logging.info('mIoU: %.4f' % (mean_IoU))

    pos = confusion_matrix.sum(1)
    res = confusion_matrix.sum(0)
    tp = np.diag(confusion_matrix)
    pixel_acc = tp.sum()/pos.sum()
    mean_acc = (tp/np.maximum(1.0, pos)).mean()
    IoU_array = (tp / np.maximum(1.0, pos + res - tp))
    mean_IoU = IoU_array.mean()

    return mean_IoU, IoU_array, pixel_acc, mean_acc


def test(config, test_dataset, testloader, model,
         sv_dir='./', sv_pred=True):
    model.eval()
    with torch.no_grad():
        for _, batch in enumerate(tqdm(testloader)):
            image, size, name = batch
            size = size[0]
            pred = test_dataset.single_scale_inference(
                config,
                model,
                image.cuda())

            if pred.size()[-2] != size[0] or pred.size()[-1] != size[1]:
                pred = F.interpolate(
                    pred, size[-2:],
                    mode='bilinear', align_corners=config.MODEL.ALIGN_CORNERS
                )
                
            if sv_pred:
                sv_path = os.path.join(sv_dir,'test_results')
                if not os.path.exists(sv_path):
                    os.mkdir(sv_path)
                test_dataset.save_pred(pred, sv_path, name)
