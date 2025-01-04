# -*- coding: utf-8 -*-
"""AML_2a

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/17vG6qcg0FTOVEtiNu2bInIqxx-IP3Kqo

Train.zip import
"""

import os
import zipfile

# URL for the dataset
url = "https://zenodo.org/records/5706578/files/Train.zip?download=1"

# Download the file using wget
!wget -O /content/Train.zip "$url"

# Define the extraction path
extract_path = '/content/datasets/Train/'

# Create the extraction directory if it doesn't exist
os.makedirs(extract_path, exist_ok=True)

# Extract the ZIP file
with zipfile.ZipFile('/content/Train.zip', 'r') as zip_ref:
    zip_ref.extractall(extract_path)

# List the contents of the extracted folder
extracted_files = os.listdir(extract_path)
print("Extracted files:", extracted_files)

import os
import zipfile

# URL for the dataset
url = "https://zenodo.org/records/5706578/files/Val.zip?download=1"

# Download the file using wget
!wget -O /content/Val.zip "$url"

# Define the extraction path
extract_path = '/content/datasets/Val/'

# Create the extraction directory if it doesn't exist
os.makedirs(extract_path, exist_ok=True)

# Extract the ZIP file
with zipfile.ZipFile('/content/Val.zip', 'r') as zip_ref:
    zip_ref.extractall(extract_path)

# List the contents of the extracted folder
extracted_files = os.listdir(extract_path)
print("Extracted files:", extracted_files)

import torch
import torch.nn as nn
import torch.nn.functional as F
from collections import OrderedDict

expansion = 4

class ConvBN(nn.Module): #Convolutional followed by Batch Norm
    def __init__(self, in_planes, out_planes, kernel_size=1, stride=1, padding=0, dilation=1):
        super(ConvBN, self).__init__()
        self.conv = nn.Conv2d(in_planes, out_planes, kernel_size=kernel_size, stride=stride,
                              padding=padding, dilation=dilation, bias=False)
        self.bn = nn.BatchNorm2d(out_planes, eps=1e-5, momentum=1e-3)

    def forward(self, x):
        return self.bn(self.conv(x))

class Bottleneck(nn.Module):
    def __init__(self, in_planes, out_planes, stride=1, dilation=1, downsample=False):
        super(Bottleneck, self).__init__()
        mid_planes = out_planes // expansion
        self.conv1 = ConvBN(in_planes, mid_planes, kernel_size=1, stride=stride)
        self.relu1 = nn.ReLU(inplace=True)
        self.conv2 = ConvBN(mid_planes, mid_planes, kernel_size=3, stride=1, padding=dilation, dilation=dilation)
        self.relu2 = nn.ReLU(inplace=True)
        self.conv3 = ConvBN(mid_planes, out_planes, kernel_size=1)
        self.relu3 = nn.ReLU(inplace=True)

        if downsample:
            self.shortcut = ConvBN(in_planes, out_planes, kernel_size=1, stride=stride)
        else:
            self.shortcut = nn.Identity()

    def forward(self, x):
        identity = self.shortcut(x)
        out = self.relu1(self.conv1(x))
        out = self.relu2(self.conv2(out))
        out = self.conv3(out)
        out += identity
        return self.relu3(out)

def make_layer(blocks, in_planes, out_planes, stride, dilation):
    layers = OrderedDict()
    layers['block1'] = Bottleneck(in_planes, out_planes, stride=stride, dilation=dilation, downsample=True)
    for i in range(1, blocks):
        layers[f'block{i+1}'] = Bottleneck(out_planes, out_planes, stride=1, dilation=dilation)
    return nn.Sequential(layers)

class ASPP(nn.Module):
    def __init__(self, in_planes, out_planes, atrous_rates):
        super(ASPP, self).__init__()
        self.convs = nn.ModuleList([
            nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=1,
                      padding=rate, dilation=rate, bias=True) for rate in atrous_rates
        ])
        self._init_weights()

    def _init_weights(self):
        for conv in self.convs:
            nn.init.normal_(conv.weight, mean=0, std=0.01)
            nn.init.constant_(conv.bias, 0)

    def forward(self, x):
        return sum(conv(x) for conv in self.convs)

class DeepLabV2_ResNet101(nn.Module):
    def __init__(self, n_classes, n_blocks, atrous_rates):
        super(DeepLabV2_ResNet101, self).__init__()
        planes = [64 * 2 ** i for i in range(6)]

        # Stem Layer
        self.layer1 = nn.Sequential(
            ConvBN(3, planes[0], kernel_size=7, stride=2, padding=3),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=3, stride=2, padding=1, ceil_mode=True)
        )

        # ResNet Backbone
        self.layer2 = make_layer(n_blocks[0], planes[0], planes[2], stride=1, dilation=1)
        self.layer3 = make_layer(n_blocks[1], planes[2], planes[3], stride=2, dilation=1)
        self.layer4 = make_layer(n_blocks[2], planes[3], planes[4], stride=1, dilation=2)
        self.layer5 = make_layer(n_blocks[3], planes[4], planes[5], stride=1, dilation=4)

        # ASPP Module
        self.aspp = ASPP(planes[5], n_classes, atrous_rates)

    def forward(self, x):
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        x = self.layer5(x)
        return self.aspp(x)

if __name__ == "__main__":
    model = DeepLabV2_ResNet101(
        n_classes=8,
        n_blocks=[3, 4, 23, 3],  # ResNet-101 block configuration
        atrous_rates=[6, 12, 18, 24]  # Atrous rates for ASPP
    )
    model.eval()
    input_tensor = torch.randn(1, 3, 1024, 1024)
    output = model(input_tensor)
    print("Input shape:", input_tensor.shape)
    print("Output shape:", output.shape)

import os
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms, models
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm
from PIL import Image
import numpy as np

# Define Dataset for Segmentation (Train and Validation)
class SimpleSegmentationDataset(Dataset):
    def __init__(self, image_dir, mask_dir, transform=None):
        self.image_dir = image_dir
        self.mask_dir = mask_dir
        self.transform = transform
        self.images = sorted(os.listdir(image_dir))
        self.masks = sorted(os.listdir(mask_dir))

    def __len__(self):
        return len(self.images)

    def __getitem__(self, idx):
        image = Image.open(os.path.join(self.image_dir, self.images[idx])).convert('RGB')
        mask = Image.open(os.path.join(self.mask_dir, self.masks[idx]))

        if self.transform:
            image = self.transform(image)
            mask = torch.tensor(np.array(mask), dtype=torch.long)  # Convert to LongTensor for CrossEntropy

        return image, mask

# Define DeepLabV2 Model
class DeepLabV2(nn.Module):
    def __init__(self, n_classes):
        super(DeepLabV2, self).__init__()
        # Use torchvision's resnet101 pretrained on ImageNet by default
        backbone = models.resnet101(pretrained=True)  # Use pretrained=True directly

        # Keep only layers up to layer4
        self.backbone = nn.Sequential(*(list(backbone.children())[:-2]))  # Exclude the final FC layer
        self.aspp = nn.ModuleList([
            nn.Conv2d(2048, 256, kernel_size=3, padding=r, dilation=r, bias=True) # list of modules with different dilation rates
            for r in [6, 12, 18, 24]
        ])
        self.classifier = nn.Conv2d(256, n_classes, kernel_size=1)

         # Add upsampling layer
        self.upsample = nn.Upsample(scale_factor=32, mode='bilinear', align_corners=True) # Upsample by 32 to match input size

    def forward(self, x):
        x = self.backbone(x)
        aspp_out = sum(aspp(x) for aspp in self.aspp) # the outputs of the four convolutions are summed together

        x = self.classifier(aspp_out) # Apply the classifier

        # Upsample the output
        x = self.upsample(x) # Apply upsampling

        return x

# Calculate IoU (Intersection over Union) for validation
def calculate_iou(output, target, num_classes):
    output = torch.argmax(output, dim=1)
    iou_list = []
    for i in range(num_classes):
        intersection = ((output == i) & (target == i)).sum().float()
        union = ((output == i) | (target == i)).sum().float()
        iou = intersection / (union + 1e-6)  # Avoid division by zero
        iou_list.append(iou.item())
    return np.mean(iou_list)

def train():
    dataset_dir = "datasets/Train/Train/Rural"
    output_dir = "checkpoints"
    os.makedirs(output_dir, exist_ok=True)
    log_dir = "logs"
    batch_size = 4
    num_classes = 8
    lr = 0.001
    epochs = 20
    save_interval = 2 # save checkpoints every tot

    # Paths for training and validation data
    train_images = os.path.join(dataset_dir, "images_png")
    train_masks = os.path.join(dataset_dir, "masks_png")
    val_dir = "datasets/Val/Val/Rural"
    val_images = os.path.join(val_dir, "images_png")
    val_masks = os.path.join(val_dir, "masks_png")

    # Data transformations
    transform = transforms.Compose([
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    # Datasets and DataLoaders
    train_dataset = SimpleSegmentationDataset(train_images, train_masks, transform)
    val_dataset = SimpleSegmentationDataset(val_images, val_masks, transform)
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=4)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=4)

    # Model Setup
    model = DeepLabV2(n_classes=num_classes)
    model = nn.DataParallel(model).cuda()

    # Optimizer and Loss
    optimizer = optim.SGD(model.parameters(), lr=lr, momentum=0.9, weight_decay=1e-4)
    criterion = nn.CrossEntropyLoss()

    # TensorBoard Setup
    writer = SummaryWriter(log_dir=log_dir)

    # Load checkpoint if it exists and start_epoch > 0
    # it will start from the next one
    start_epoch = 0  # Change to 0 to start without using checkpoints
    checkpoint_path = os.path.join(output_dir, f"model_epoch_{start_epoch}.pth")

    if start_epoch > 0 and os.path.exists(checkpoint_path):
        checkpoint = torch.load(checkpoint_path)
        if 'model_state_dict' in checkpoint:
            model.load_state_dict(checkpoint['model_state_dict'])
            optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
            start_epoch = checkpoint['epoch'] + 1  # Continue from the next epoch
            print(f"Resuming from epoch {start_epoch}")

            if 'loss' in checkpoint:
                print(f"Last saved loss: {checkpoint['loss']:.4f}")
        else:
            print(f"Checkpoint file {checkpoint_path} does not contain 'model_state_dict'. Starting from epoch 1.")



    import matplotlib.pyplot as plt

    def visualize_predictions(images, predictions, ground_truths, num_classes):
      for idx, (image, pred, gt) in enumerate(zip(images, predictions, ground_truths)):
          plt.figure(figsize=(10, 5))
          plt.subplot(1, 3, 1)
          plt.title("Image")
          plt.imshow(image.permute(1, 2, 0).cpu().numpy())  # Original input image
          plt.subplot(1, 3, 2)
          plt.title("Prediction")
          plt.imshow(pred.cpu().numpy(), cmap='tab20', vmin=0, vmax=num_classes-1)  # Prediction mask
          plt.subplot(1, 3, 3)
          plt.title("Ground Truth")
          plt.imshow(gt.cpu().numpy(), cmap='tab20', vmin=0, vmax=num_classes-1)  # Actual ground truth mask
          plt.show()



    # Training Loop
    for epoch in range(start_epoch, epochs):
        running_loss = 0.0
        for images, masks in tqdm(train_loader, desc=f"Epoch {epoch+1}/{epochs}"):
            images, masks = images.cuda(), masks.cuda()

            optimizer.zero_grad()
            outputs = model(images)
            loss = criterion(outputs, masks)
            loss.backward()
            optimizer.step()

            running_loss += loss.item()

        avg_loss = running_loss / len(train_loader)
        writer.add_scalar("Loss/train", avg_loss, epoch)
        print(f"Epoch [{epoch+1}/{epochs}], Loss: {avg_loss:.4f}")




        # Validate after each epoch
        model.eval()
        val_loss = 0.0
        val_iou = 0.0

        visualize_batch = True  # Toggle to control visualization during training





        with torch.no_grad():
            for images, masks in tqdm(val_loader, desc="Validation"):

                images, masks = images.cuda(), masks.cuda()

                outputs = model(images)
                loss = criterion(outputs, masks)
                val_loss += loss.item()

                iou = calculate_iou(outputs, masks, num_classes)
                val_iou += iou

                # Visualize predictions for the first batch
                if visualize_batch:
                    visualize_predictions(
                        images=images.cpu(),  # Detach from GPU
                        predictions=torch.argmax(outputs, dim=1).cpu(),
                        ground_truths=masks.cpu(),
                        num_classes=num_classes
                    )
                    visualize_batch = False  # Prevent visualizing every batch



        avg_val_loss = val_loss / len(val_loader)
        avg_val_iou = val_iou / len(val_loader)
        writer.add_scalar("Loss/val", avg_val_loss, epoch)
        writer.add_scalar("IoU/val", avg_val_iou, epoch)
        print(f"Validation - Loss: {avg_val_loss:.4f}, IoU: {avg_val_iou:.4f}")


        # Save Model Checkpoint
        if (epoch + 1) % save_interval == 0:
            checkpoint_path = os.path.join(output_dir, f"model_epoch_{epoch+1}.pth")
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'loss': avg_loss,
            }, checkpoint_path)



    writer.close()

# Start training the model
if __name__ == "__main__":
    train()