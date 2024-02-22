import torch
import torch.nn.functional as F
import sys
import os
path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if path not in sys.path:sys.path.append(path)
import matplotlib.pyplot as plt
from PIL import Image
import numpy as np
from config import cfg

class MatchingClassifier(torch.nn.Module):
    def __init__(self, top_k=1, seed=-1):
        super(MatchingClassifier, self).__init__()
        self.seed = seed

    def forward(self, support_features, query_features, support_labels, query_labels, use_cosine=True):
        # support_features: list of features, as a tensor of shape [Ns,d] where Ns is the number of support features
        # support_labels: list of (class,image_index) tuples
        # query_features: list of features, as a tensor of shape [Nq,d] where Nq is the number of query features
        # query_labels: list of (class,image_index) tuples
        # output: acc[:cfg.sampler.n_shots], accuracy for each n_shot
        # calculate similarity between each query feature and each support feature (between crops)
        if use_cosine:
            similarity = F.cosine_similarity(query_features.unsqueeze(1), support_features.unsqueeze(0), dim=2) # [n_query, n_shot]
        else:
            similarity = -torch.cdist(query_features.unsqueeze(1), support_features.unsqueeze(0), p=2)
        #similarity (Nq, Ns)!= (n_query, n_shot)
        # problem is we can have multiple features per image (as we have multiple masks)
        similarity = similarity.squeeze(1) # [Nq, Ns]
        # unique_support_labels = {image_index: class_index}
        unique_labels = [] # list of unique class indices
        unique_support_labels = {} # class_index: [image_index]
        unique_support_labels_reverse = {} # image_index: class_index
        unique_query_labels = {} # class_index: [image_index]
        unique_query_labels_reverse = {} # image_index: class_index
        annotations = {} # image_index: [crop_index]
        annotations['support'] = {}
        annotations['query'] = {}
        annotations_reverse = {} # crop_index:image_index
        annotations_reverse["support"] = {}
        annotations_reverse["query"] = {}
        # Construct unique labels and annotations
        for i, (class_index, image_index) in enumerate(support_labels):
            if class_index not in unique_labels:
                unique_labels.append(class_index)
            if class_index not in unique_support_labels:
                unique_support_labels[class_index] = []
            if image_index not in unique_support_labels[class_index]:
                unique_support_labels[class_index].append(image_index)
            if image_index not in unique_support_labels_reverse:
                unique_support_labels_reverse[image_index] = class_index
            if image_index not in annotations['support']:
                annotations['support'][image_index] = []
            annotations['support'][image_index].append(i)
            if i not in annotations_reverse["support"]:
                annotations_reverse["support"][i] = image_index
        for i, (class_index, image_index) in enumerate(query_labels):
            if class_index not in unique_query_labels:
                unique_query_labels[class_index] = []
            if image_index not in unique_query_labels[class_index]:
                unique_query_labels[class_index].append(image_index)
            if image_index not in unique_query_labels_reverse:
                unique_query_labels_reverse[image_index] = class_index
            if image_index not in annotations['query']:
                annotations['query'][image_index] = []
            annotations['query'][image_index].append(i)
            if i not in annotations_reverse["query"]:
                annotations_reverse["query"][i] = image_index
                
        acc = np.zeros(cfg.sampler.n_shots) # accuracy for each number of shots
        for image_index in annotations['query']:
            for n_shot in range(cfg.sampler.n_shots):
                # sample k+1 indices from unique_support_labels[class_index]
                sampled_support_indices = {}
                for support_class_index in unique_labels:
                    if self.seed > 0:
                        np.random.seed(self.seed)
                    sampled_support_indices[support_class_index] = np.random.choice(unique_support_labels[support_class_index], n_shot+1, replace=False)
                augmented_support_indices = []
                k_class = {} # Number of support crops per class
                for support_class_index in unique_labels:
                    for index in sampled_support_indices[support_class_index]:
                        for j in annotations['support'][index]:
                            augmented_support_indices.append(j)
                            if support_class_index not in k_class:
                                k_class[support_class_index] = 0
                            k_class[support_class_index] += 1
                k_min= np.min(list(k_class.values())) # minimum number of support crops per class for knn
                augmented_query_indices = []
                for j in annotations['query'][image_index]:
                    augmented_query_indices.append(j)
                similarity_sampled = similarity[augmented_query_indices][:,augmented_support_indices] # [n_query, n_shot]
                """max_similarity = -float('inf')
                max_similarity_index = -float('inf')
                for j in range(len(augmented_query_indices)):
                    for l in range(len(augmented_support_indices)):
                        if similarity_sampled[j,l] > max_similarity and l!=1:
                            max_similarity = similarity_sampled[j,l]
                            max_similarity_index = l
                support_crop_index = augmented_support_indices[max_similarity_index]
                support_image_index = annotations_reverse["support"][support_crop_index]
                support_class = unique_support_labels_reverse[support_image_index]"""
                
                topk_similarity, topk_indices = torch.topk(similarity_sampled.flatten(), k_min) #along the support set
                topk_indices = topk_indices%similarity_sampled.shape[1] # indices of the topk crops
                topk_crop_indices = [augmented_support_indices[i] for i in topk_indices] # indices of the topk crops
                topk_image_indices = [annotations_reverse["support"][i] for i in topk_crop_indices] # indices of the topk images
                topk_classes = [unique_support_labels_reverse[i] for i in topk_image_indices] # classes of the topk images
                classes_count = {} # count of each class
                for i, c in enumerate(topk_classes):
                    if c not in classes_count:
                        classes_count[c] = [0,0]
                    classes_count[c][0] += 1 # count of each class
                    classes_count[c][1] += topk_similarity[i] # sum of similarities to solve ties
                
                max_count = max([classes_count[c][0] for c in classes_count])
                max_count_classes = [c for c in classes_count if classes_count[c][0] == max_count] # classes with the maximum count
                max_count_classes_distances = {c:classes_count[c][1] for c in max_count_classes} # sum of similarities for each class with the maximum count
                support_class = max(max_count_classes_distances, key=max_count_classes_distances.get) # class with the maximum sum of similarities when there are ties
                if support_class == unique_query_labels_reverse[image_index]:
                    acc[n_shot] += 1
        acc = acc / len(annotations['query'])
        return acc[0]
class NCM(torch.nn.Module):
    def __init__(self, top_k=1, seed=-1):
        super(NCM, self).__init__()
        self.seed = seed

    def forward(self, support_features, query_features, support_labels, query_labels, calculate_accuracy=True, use_cosine=True, to_display=None):
        # support_features: list of features, as a tensor of shape [Ns,d] where Ns is the number of support features
        # support_labels: list of (class,image_index) tuples
        # query_features: list of features, as a tensor of shape [Nq,d] where Nq is the number of query features
        # query_labels: list of (class,image_index)
        # output: acc[:cfg.sampler.n_shots], accuracy for each n_shot
        unique_labels = []
        unique_support_labels = {} # class_index: [image_index]
        unique_support_labels_reverse = {} # image_index: class_index
        unique_query_labels = {} # class_index: [image_index]
        unique_query_labels_reverse = {} # image_index: class_index
        annotations = {} # image_index: [crop_index]
        annotations['support'] = {}
        annotations['query'] = {}
        annotations_reverse = {} # crop_index:image_index
        annotations_reverse["support"] = {}
        annotations_reverse["query"] = {}
        for i, (class_index, image_index) in enumerate(support_labels):
            if class_index not in unique_labels:
                unique_labels.append(class_index)
            if class_index not in unique_support_labels:
                unique_support_labels[class_index] = []
            if image_index not in unique_support_labels[class_index]:
                unique_support_labels[class_index].append(image_index)
            if image_index not in unique_support_labels_reverse:
                unique_support_labels_reverse[image_index] = class_index
            if image_index not in annotations['support']:
                annotations['support'][image_index] = []
            annotations['support'][image_index].append(i)
            if i not in annotations_reverse["support"]:
                annotations_reverse["support"][i] = image_index
        for i, (class_index, image_index) in enumerate(query_labels):
            if class_index not in unique_query_labels:
                unique_query_labels[class_index] = []
            if image_index not in unique_query_labels[class_index]:
                unique_query_labels[class_index].append(image_index)
            if image_index not in unique_query_labels_reverse:
                unique_query_labels_reverse[image_index] = class_index
            if image_index not in annotations['query']:
                annotations['query'][image_index] = []
            annotations['query'][image_index].append(i)
            if i not in annotations_reverse["query"]:
                annotations_reverse["query"][i] = image_index
        prototypes = {}
        for class_index in unique_labels:
            img_indices = unique_support_labels[class_index]
            crop_indices = []
            for img_index in img_indices:
                for j in annotations['support'][img_index]:
                    crop_indices.append(j)
            prototypes[class_index] = torch.mean(support_features[crop_indices], dim=0)
            


def preprocess_plot(img):
    img = (img - img.min()) / (img.max() - img.min())
    img = (img * 255).astype('uint8')
    return img


def test():
    # Test NCM
    ncm = NCM()
    support_features = torch.randn(5*20*3, 10)
    query_features = torch.randn(20*5*3, 10)
    support_labels = [(i//(20*3), i//3) for i in range(5*20*3)]
    query_labels = [(i//(20*3), i//3) for i in range(20*5*3)]
    acc = ncm(support_features, query_features, support_labels, query_labels)
    print(acc)
def test2():
    # Test NCM
    ncm = NCM()
    support_features = torch.randn(5*2*3, 10)
    query_features = torch.randn(15*5*3, 10)
    support_labels = [(i//(2*3), i//3) for i in range(5*2*3)]
    query_labels = [(i//(15*3), i//3) for i in range(15*5*3)]
    support_augmented_imgs = [torch.randn(1,3, 224, 224) for i in range(5*2*3)]
    query_augmented_imgs = [torch.randn(1,3, 224, 224) for i in range(15*5*3)]
    support_augmented_imgs = [img.squeeze(0).permute(1,2,0).cpu().numpy() for img in support_augmented_imgs]
    query_augmented_imgs = [img.squeeze(0).permute(1,2,0).cpu().numpy() for img in query_augmented_imgs]
    to_display = (support_augmented_imgs, query_augmented_imgs)
    acc = ncm(support_features, query_features, support_labels, query_labels, to_display=to_display)
    print(acc)
def test3():
    ncm = NCM()
    support_features = torch.randn(5*20*3, 10)
    query_features = torch.randn(15*5*3, 10)
    support_labels = [(i//(20*3), i//3) for i in range(5*20*3)]
    query_labels = [(i//(15*3), i//3) for i in range(15*5*3)]
    support_augmented_imgs = [torch.randn(1,3, 224, 224) for i in range(5*20*3)]
    query_augmented_imgs = [torch.randn(1,3, 224, 224) for i in range(15*5*3)]
    support_augmented_imgs = [img.squeeze(0).permute(1,2,0).cpu().numpy() for img in support_augmented_imgs]
    query_augmented_imgs = [img.squeeze(0).permute(1,2,0).cpu().numpy() for img in query_augmented_imgs]
    similarity = ncm(support_features, query_features, support_labels, query_labels,calculate_accuracy=False,use_cosine=True)
    
if __name__ == '__main__':
    test()
