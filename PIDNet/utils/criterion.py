# ------------------------------------------------------------------------------
# Modified based on https://github.com/HRNet/HRNet-Semantic-Segmentation
# ------------------------------------------------------------------------------
import torch
import torch.nn as nn
from torch.nn import functional as F
from configs import config


class CrossEntropy(nn.Module):
    def __init__(self, ignore_label=-1, weight=None):
        super(CrossEntropy, self).__init__()
        self.ignore_label = ignore_label
        self.criterion = nn.CrossEntropyLoss(
            weight=weight,
            ignore_index=ignore_label
        )

    def _forward(self, score, target):

        loss = self.criterion(score, target)

        return loss

    def forward(self, score, target):

        if config.MODEL.NUM_OUTPUTS == 1:
            score = [score]

        balance_weights = config.LOSS.BALANCE_WEIGHTS
        sb_weights = config.LOSS.SB_WEIGHTS
        if len(balance_weights) == len(score):
            return sum([w * self._forward(x, target) for (w, x) in zip(balance_weights, score)])
        elif len(score) == 1:
            return sb_weights * self._forward(score[0], target)
        
        else:
            raise ValueError("lengths of prediction and target are not identical!")

        


class OhemCrossEntropy(nn.Module):
    def __init__(self, ignore_label=-1, thres=0.7,
                 min_kept=100000, weight=None):
        super(OhemCrossEntropy, self).__init__()
        self.thresh = thres
        self.min_kept = max(1, min_kept)
        self.ignore_label = ignore_label
        self.criterion = nn.CrossEntropyLoss(
            weight=weight,
            ignore_index=ignore_label,
            reduction='none'
        )

    def _ce_forward(self, score, target):


        loss = self.criterion(score, target)

        return loss

    def _ohem_forward(self, score, target, **kwargs):
        
        pred = F.softmax(score, dim=1)
        pixel_losses = self.criterion(score, target).contiguous().view(-1)
        mask = target.contiguous().view(-1) != self.ignore_label
        tmp_target = target.clone()
        tmp_target[tmp_target == self.ignore_label] = 0
        pred = pred.gather(1, tmp_target.unsqueeze(1))
        pred, ind = pred.contiguous().view(-1,)[mask].contiguous().sort()
        min_value = pred[min(self.min_kept, pred.numel() - 1)]
        threshold = max(min_value, self.thresh)

        pixel_losses = pixel_losses[mask][ind]
        pixel_losses = pixel_losses[pred < threshold]
        return pixel_losses.mean()

    def forward(self, score, target):
        
        if not (isinstance(score, list) or isinstance(score, tuple)):
            score = [score]

        balance_weights = config.LOSS.BALANCE_WEIGHTS
        sb_weights = config.LOSS.SB_WEIGHTS
        if len(balance_weights) == len(score):
            functions = [self._ce_forward] * \
                (len(balance_weights) - 1) + [self._ohem_forward]
            return sum([
                w * func(x, target)
                for (w, x, func) in zip(balance_weights, score, functions)
            ])
        
        elif len(score) == 1:
            return sb_weights * self._ohem_forward(score[0], target)
        
        else:
            raise ValueError("lengths of prediction and target are not identical!")


def weighted_bce(bd_pre, target):
    n, c, h, w = bd_pre.size()
    log_p = bd_pre.permute(0,2,3,1).contiguous().view(1, -1)
    target_t = target.view(1, -1)

    pos_index = (target_t == 1)
    neg_index = (target_t == 0)

    weight = torch.zeros_like(log_p)
    pos_num = pos_index.sum()
    neg_num = neg_index.sum()
    sum_num = pos_num + neg_num
    weight[pos_index] = neg_num * 1.0 / sum_num
    weight[neg_index] = pos_num * 1.0 / sum_num

    loss = F.binary_cross_entropy_with_logits(log_p, target_t, weight, reduction='mean')

    return loss


class BondaryLoss(nn.Module):
    def __init__(self, coeff_bce = 20.0):
        super(BondaryLoss, self).__init__()
        self.coeff_bce = coeff_bce
        
    def forward(self, bd_pre, bd_gt):

        bce_loss = self.coeff_bce * weighted_bce(bd_pre, bd_gt)
        loss = bce_loss
        
        return loss
    

class DiceLoss(nn.Module):
    def __init__(self, eps=1e-6, ignore_label=-1):
        super(DiceLoss, self).__init__()
        self.eps = eps
        self.ignore_label = ignore_label

    def forward(self, preds, targets):
      # process each tensor individually
      if isinstance(preds, list):
          preds = torch.cat(preds, dim=1)  # Combine list into a single tensor along channel dimension
      
      # Proceed with the existing logic
      if preds.shape[1] > 1:
          preds = F.softmax(preds, dim=1)
      num_classes = preds.shape[1]
      targets_one_hot = F.one_hot(targets, num_classes=num_classes).permute(0, 3, 1, 2).float()
      preds = preds[:, 1:]
      targets_one_hot = targets_one_hot[:, 1:]
      intersection = torch.sum(preds * targets_one_hot, dim=(2, 3))
      union = torch.sum(preds, dim=(2, 3)) + torch.sum(targets_one_hot, dim=(2, 3))
      dice_score = (2.0 * intersection + self.eps) / (union + self.eps)
      loss = 1.0 - dice_score.mean()
      return loss

    
class FocalLoss(nn.Module):
    def __init__(self, alpha=1, gamma=2, ignore_label=0, weight=None):
        
        super(FocalLoss, self).__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.ignore_label = ignore_label
        self.criterion = nn.CrossEntropyLoss(weight=weight, ignore_index=ignore_label, reduction='none')

    def forward(self, score, target):
        
        # Compute standard cross-entropy loss
        ce_loss = self.criterion(score, target)

        # Compute probability of the target class
        probs = F.softmax(score, dim=1)
        target_probs = probs.gather(1, target.unsqueeze(1)).squeeze(1)

        # Apply the focal loss formula
        focal_weight = self.alpha * (1 - target_probs) ** self.gamma
        focal_loss = focal_weight * ce_loss

        # Return the average loss
        return focal_loss.mean()
    
if __name__ == '__main__':
    # Import required modules
    import torch
    import torch.nn.functional as F
    

    # Initialize dummy inputs
    batch_size, num_classes, height, width = 4, 5, 16, 16


    # Predictions and targets for DiceLoss
    dice_preds = torch.randn(batch_size, num_classes, height, width, requires_grad=True)
    dice_targets = torch.randint(0, num_classes, (batch_size, height, width))

    # Initialize the losses
    dice_loss_with_ignore_label = DiceLoss(ignore_label=0)

    
    

    try:
        dice_loss_with_ignore_label_value = dice_loss_with_ignore_label(dice_preds, dice_targets)
        print(f"Dice Loss With Ignore Label: {dice_loss_with_ignore_label_value.item()}")
    except Exception as e:
        print(f"Dice Loss With Ignore Label failed: {e}")



        
        
        


