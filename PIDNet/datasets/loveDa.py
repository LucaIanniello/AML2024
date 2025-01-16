# ------------------------------------------------------------------------------
# Modified based on https://github.com/HRNet/HRNet-Semantic-Segmentation
# ------------------------------------------------------------------------------

import os

import cv2
import numpy as np
from PIL import Image

import torch
from .base_dataset import BaseDataset
import albumentations as A
from configs import config

class Loveda(BaseDataset):
    def __init__(self, 
                 root, 
                 list_path,
                 num_classes=8,
                 multi_scale=True, 
                 flip=True, 
                 ignore_label=255, 
                 base_size=2048, 
                 crop_size=(512, 1024),
                 scale_factor=16,
                 mean=[0.485, 0.456, 0.406], 
                 std=[0.229, 0.224, 0.225],
                 bd_dilate_size=4):

        super(Loveda, self).__init__(ignore_label, base_size,
                crop_size, scale_factor, mean, std,)

        self.root = root
        self.list_path = list_path
        self.num_classes = num_classes

        self.multi_scale = multi_scale
        self.flip = flip
        
        self.img_list = [line.strip().split() for line in open(root+list_path)]

        self.files = self.read_files()

        self.label_mapping = {0: ignore_label,
                              1: 1, 2: 2, 
                              3: 3, 4: 4, 
                              5: 5, 6: 6 , 7:7}
        
        self.class_weights = torch.FloatTensor([0.1,0.1,0.1,0.1,0.1,0.1,0.1,0.1]).cuda()
        
        transforms_list = []
        if config.TRAIN.AUG1:
            transforms_list.append(A.HorizontalFlip(p=0.5))
        if config.TRAIN.AUG2:
            transforms_list.append(A.GaussianBlur(blur_limit=(3, 7), p=0.5))
        if config.TRAIN.AUG3:
            transforms_list.append(A.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, p=0.5))
        if config.TRAIN.AUG4:
            transforms_list.append(A.RandomBrightnessContrast(brightness_limit=0.2, contrast_limit=0.2, p=0.5))

        self.transform = A.Compose(transforms_list)


        self.bd_dilate_size = bd_dilate_size
    
    def read_files(self):
        files = []
        if 'test' in self.list_path:
            for item in self.img_list:
                image_path = item
                name = os.path.splitext(os.path.basename(image_path[0]))[0]
                files.append({
                    "img": image_path[0],
                    "name": name,
                })
        else:
            for item in self.img_list:
                image_path, label_path = item
                name = os.path.splitext(os.path.basename(label_path))[0]
                files.append({
                    "img": image_path,
                    "label": label_path,
                    "name": name
                })
        return files

    def convert_label(self, label, inverse=False):
        temp = label.copy()
        if inverse:
            for v, k in self.label_mapping.items():
                label[temp == k] = v
        else:
            for k, v in self.label_mapping.items():
                label[temp == k] = v
        return label


    def __getitem__(self, index):
        item = self.files[index]
        name = item["name"]
        image = cv2.imread(os.path.join(self.root,'loveDa',item["img"]),
                           cv2.IMREAD_COLOR)
        size = image.shape

        if 'test' in self.list_path:
            image = self.input_transform(image)
            image = image.transpose((2, 0, 1))

            return image.copy(), np.array(size), name

        label = cv2.imread(os.path.join(self.root,'loveDa',item["label"]),
                           cv2.IMREAD_GRAYSCALE)
        
       # Applicazione delle augmentation
        if config.TRAIN.AUG:  # Controlla se le augmentazioni sono abilitate
            transformed = self.transform(image=image, mask=label)
            image = transformed['image']
            label = transformed['mask']


        # If AUG_CHANCE is enabled, return with 50% chance two times the augmented image
        if config.TRAIN.AUG_CHANCE and np.random.rand() > 0.5:

            # Apply transformations to get the augmented image
            transformed = self.transform(image=image, mask=label)
            augmented_image = transformed['image']
            augmented_label = transformed['mask']

            # We return both the original and augmented image here
            augmented_image, augmented_label, _ = self.gen_sample(augmented_image, augmented_label, 
                                                                self.multi_scale, self.flip, edge_size=self.bd_dilate_size)

            # Return both image from TRAIN.AUG and FROM AUG_CHANCE
            return image.copy(), label.copy(), edge.copy(), np.array(size), name, \
                augmented_image.copy(), augmented_label.copy(), edge.copy(), np.array(size), name


        label = self.convert_label(label)

        image, label, edge = self.gen_sample(image, label, 
                                self.multi_scale, self.flip, edge_size=self.bd_dilate_size)

        return image.copy(), label.copy(), edge.copy(), np.array(size), name, \
            -1, -1, -1, -1, -1
            
        

        




    
    def single_scale_inference(self, config, model, image):
        pred = self.inference(config, model, image)
        return pred


    def save_pred(self, preds, sv_path, name):
        preds = np.asarray(np.argmax(preds.cpu(), axis=1), dtype=np.uint8)
        for i in range(preds.shape[0]):
            pred = self.convert_label(preds[i], inverse=True)
            save_img = Image.fromarray(pred)
            save_img.save(os.path.join(sv_path, name[i]+'.png'))

        