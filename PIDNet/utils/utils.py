# ------------------------------------------------------------------------------
# Modified based on https://github.com/HRNet/HRNet-Semantic-Segmentation
# ------------------------------------------------------------------------------

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import logging
import time
from pathlib import Path

import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F
from configs import config

import sys
import os
from contextlib import contextmanager


import matplotlib.pyplot as plt
import numpy as np
from torchvision.utils import make_grid

class FullModel(nn.Module):

  def __init__(self, model, sem_loss, bd_loss):
    super(FullModel, self).__init__()
    self.model = model
    self.sem_loss = sem_loss
    self.bd_loss = bd_loss

  def pixel_acc(self, pred, label):
    _, preds = torch.max(pred, dim=1)
    valid = (label >= 0).long()
    acc_sum = torch.sum(valid * (preds == label).long())
    pixel_sum = torch.sum(valid)
    acc = acc_sum.float() / (pixel_sum.float() + 1e-10)
    return acc

  def forward(self, inputs, labels, bd_gt, *args, **kwargs):
    
    outputs = self.model(inputs, *args, **kwargs)
    inputs.cuda()
    labels.cuda()
    bd_gt.cuda()

    h, w = labels.size(1), labels.size(2)
    ph, pw = outputs[0].size(2), outputs[0].size(3)
    if ph != h or pw != w:
        for i in range(len(outputs)):
            outputs[i] = F.interpolate(outputs[i], size=(
                h, w), mode='bilinear', align_corners=config.MODEL.ALIGN_CORNERS)

    acc  = self.pixel_acc(outputs[-2], labels)
    loss_s = self.sem_loss(outputs[:-1], labels)
    loss_b = self.bd_loss(outputs[-1], bd_gt)

    filler = torch.ones_like(labels) * config.TRAIN.IGNORE_LABEL
    try:
        bd_label = torch.where(torch.sigmoid(outputs[-1][:,0,:,:]) > 0.7, labels, filler)
        loss_sb = self.sem_loss([outputs[-2]], bd_label)
    except:
        loss_sb = self.sem_loss([outputs[-2]], labels)
    loss = loss_s + loss_b + loss_sb

    return torch.unsqueeze(loss,0), outputs[:-1], acc, [loss_s, loss_b]


class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self):
        self.initialized = False
        self.val = None
        self.avg = None
        self.sum = None
        self.count = None

    def initialize(self, val, weight):
        self.val = val
        self.avg = val
        self.sum = val * weight
        self.count = weight
        self.initialized = True

    def update(self, val, weight=1):
        if not self.initialized:
            self.initialize(val, weight)
        else:
            self.add(val, weight)

    def add(self, val, weight):
        self.val = val
        self.sum += val * weight
        self.count += weight
        self.avg = self.sum / self.count

    def value(self):
        return self.val

    def average(self):
        return self.avg

def create_logger(cfg, cfg_name, phase='train'):
    root_output_dir = Path(cfg.OUTPUT_DIR)
    # set up logger
    if not root_output_dir.exists():
        print('=> creating {}'.format(root_output_dir))
        root_output_dir.mkdir()

    dataset = cfg.DATASET.DATASET
    model = cfg.MODEL.NAME
    cfg_name = os.path.basename(cfg_name).split('.')[0]

    final_output_dir = root_output_dir / dataset / cfg_name

    print('=> creating {}'.format(final_output_dir))
    final_output_dir.mkdir(parents=True, exist_ok=True)

    time_str = time.strftime('%Y-%m-%d-%H-%M')
    log_file = '{}_{}_{}.log'.format(cfg_name, time_str, phase)
    final_log_file = final_output_dir / log_file
    head = '%(asctime)-15s %(message)s'
    logging.basicConfig(filename=str(final_log_file),
                        format=head)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    console = logging.StreamHandler()
    logging.getLogger('').addHandler(console)

    tensorboard_log_dir = Path(cfg.LOG_DIR) / dataset / model / \
            (cfg_name + '_' + time_str)
    print('=> creating {}'.format(tensorboard_log_dir))
    tensorboard_log_dir.mkdir(parents=True, exist_ok=True)

    return logger, str(final_output_dir), str(tensorboard_log_dir)

def get_confusion_matrix(label, pred, size, num_class, ignore=-1):
    """
    Calcute the confusion matrix by given label and pred
    """
    output = pred.cpu().numpy().transpose(0, 2, 3, 1)
    seg_pred = np.asarray(np.argmax(output, axis=3), dtype=np.uint8)
    seg_gt = np.asarray(
    label.cpu().numpy()[:, :size[-2], :size[-1]], dtype=int)

    ignore_index = seg_gt != ignore
    seg_gt = seg_gt[ignore_index]
    seg_pred = seg_pred[ignore_index]

    index = (seg_gt * num_class + seg_pred).astype('int32')
    label_count = np.bincount(index)
    confusion_matrix = np.zeros((num_class, num_class))

    for i_label in range(num_class):
        for i_pred in range(num_class):
            cur_index = i_label * num_class + i_pred
            if cur_index < len(label_count):
                confusion_matrix[i_label,
                                 i_pred] = label_count[cur_index]
    return confusion_matrix

def adjust_learning_rate(optimizer, base_lr, max_iters, 
        cur_iters, power=0.9, nbb_mult=10):
    lr = base_lr*((1-float(cur_iters)/max_iters)**(power))
    optimizer.param_groups[0]['lr'] = lr
    if len(optimizer.param_groups) == 2:
        optimizer.param_groups[1]['lr'] = lr * nbb_mult
    return lr

@contextmanager
def suppress_stdout():
    with open(os.devnull, 'w') as fnull:
        old_stdout = sys.stdout
        sys.stdout = fnull
        try:
            yield
        finally:
            sys.stdout = old_stdout
            


def denormalize(tensor, mean, std):
  for i in range(len(mean)):
    tensor[i] = tensor[i]*std[i] + mean[i]
  return tensor
            
def visualize_images(image_tensor):
    # Ensure tensor is on CPU and denormalize
    image_tensor = image_tensor.cpu()
    image_tensor = denormalize(image_tensor, [0.485, 0.456, 0.406], [0.229, 0.224, 0.225])

    # Debug: Print the shape of the tensor
    print(f"Image tensor shape before permute: {image_tensor.shape}")

    # Handle different tensor shapes
    if image_tensor.dim() == 4:  # Batch of images (B, C, H, W)
        # Permute to (B, H, W, C)
        batch_images = image_tensor.permute(0, 2, 3, 1).numpy()  # Reorder dimensions
        for img in batch_images:
            plt.imshow(img)
            plt.show()
    elif image_tensor.dim() == 3:  # Single image (C, H, W)
        # Permute to (H, W, C)
        image = image_tensor.permute(1, 2, 0).numpy()  # Reorder dimensions
        plt.imshow(image)
        plt.show()
    else:
        raise ValueError(f"Unexpected tensor dimensions: {image_tensor.dim()}")


  
def visualize_segmentation(segmentation_tensor):
    # Sposta il tensor sulla CPU e converti in numpy array
    seg_map = segmentation_tensor.cpu().numpy()
    
    # Definisci una mappa colori per 8 classi (0-7)
    # Ogni colore è in formato RGB
    color_map = {
        0: [0, 0, 0],        # Nero Tutto il resto
        1: [255, 0, 0],      # Rosso Background
        2: [0, 255, 0],      # Verde Building
        3: [0, 0, 255],      # Blu Road 
        4: [255, 255, 0],    # Giallo Water
        5: [255, 0, 255],    # Magenta Barren
        6: [0, 255, 255],    # Ciano Forest
        7: [128, 128, 128]   # Grigio Agricolture
    }
    
    # Crea un'immagine RGB vuota
    height, width = seg_map.shape
    colored_seg = np.zeros((height, width, 3), dtype=np.uint8)
    
    # Assegna i colori in base al valore della classe
    for class_idx in range(8):
        mask = (seg_map == class_idx)
        colored_seg[mask] = color_map[class_idx]
    
    # Visualizza l'immagine
    plt.figure(figsize=(10, 10))
    plt.imshow(colored_seg)
    plt.axis('off')
    plt.show()