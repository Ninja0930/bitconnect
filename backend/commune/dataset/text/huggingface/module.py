import streamlit as st
from random import shuffle, seed
from collections import defaultdict
import pandas as pd
import torch
from torch import nn
from tqdm.auto import tqdm
from torch.nn import CrossEntropyLoss
import torch.nn.functional as F
# import torchsort
from commune import Module
import ray
import asyncio
from munch import Munch

# from commune.bittensor.cortex.metric import causal_lm_loss, ranknet_loss
from commune.utils import *
from sklearn import metrics
from scipy.stats import kendalltau
import torch
from torch import nn
from commune.ray.actor_pool import ActorPool
Module.new_event_loop()
import bittensor
import datasets

class DatasetModule(Module):
    def __init__(self,
                path:str=None,
                name:str = None,
                split:str=None,
                tokenizer:'tokenizer'=None, 
                config: dict=None, 
                **kwargs):
        Module.__init__(self, config=config, **kwargs)
        self.load_tokenizer(tokenizer=tokenizer)
        self.load_dataset(path=path, name=name, split=split)

    def load_tokenizer(self, tokenizer=None): 
        tokenizer = tokenizer if tokenizer else self.config['tokenizer']
        self.tokenizer = self.launch_module(**tokenizer)
        return self.tokenizer

    def load_dataset(self, path:str=None, name:str=None, split:str=None):
        
        self.path = path if path  else self.path
        self.name = name if name  else self.name
        self.split = split if split  else self.split

        kwargs = {'name': self.name, 'path': self.path, 'split': self.split}

        dataset_list = self.launch_module(module='datasets.load_dataset', kwargs=kwargs)
        dataset_map = {}
        self.dataset = {}
        for dataset in dataset_list: 
            self.dataset[str(dataset.split)] = dataset
        return self.dataset

    def filter_dataset(self, fn, dataset=None):
        if dataset == None:
            dataset = self.dataset
        for split in dataset.keys():
            dataset[split] = dataset[split].filter(fn)
        return dataset

    @property
    def info(self):
        return self.dataset[self.split[0]]._info.__dict__

    @property
    def features(self):
        return self.dataset[self.split[0]]._info.__dict__['features']

    @property
    def device(self):
        device = self.config.get('device', 'cpu')
        if 'cuda' in device:
            assert torch.cuda.is_available()
        return device

    @device.setter
    def device(self, device):
        if 'cuda' in device:
            assert torch.cuda.is_available()
        self.config['device'] = device
        return device

    def to(self, device):
        self.device = device
        return self.device

    default_receptor_path = 'bittensor.receptor.pool.module.ReceptorPoolModule'

    def tokenize(self, text, padding=True, *args, **kwargs):
        device = kwargs.pop('device', self.device)
        return torch.tensor(self.tokenizer(text=text, padding=padding)['input_ids']).to(device)


    default_split = ['train']
    @property
    def split(self):
        split = self.config.get('split', self.default_split)
        st.write(self.config['split'])
        return split

    @split.setter
    def split(self, value):
        st.write(value, 'split')
        if isinstance(value, str):
            value = [value]
        assert isinstance(value, list), f'{value} should be a string'
        self.config['split'] = value

    def split_size(self, split):
        return len(self.dataset[split])

    def split_size_map(self):
        return {split: self.dataset[split] for split in self.split}

    def resolve_split(self, split):
        if split == None:
            split = self.split[0]
        return split

    def __getitem__(self, idx=None, split='train', sequence_length=128):
        
        split = self.resolve_split(split) 
        dataset_length =  self.split_size(split)
        if idx == None:
            idx = random.randint(1,dataset_length-1)    

        final_sample  = ''
        while len(final_sample.split()) < sequence_length:
            split = self.resolve_split(split)
            sample = self.dataset[split][idx].get(self.text_field)
            assert sample != None, f'Please specify a valid text_field {self.dataset[split][idx]}'

            final_sample += sample if len(final_sample) == 0 else '\n' + sample
            idx = (idx + 1 ) % dataset_length
        final_sample = ' '.join(final_sample.split()[:sequence_length])
        return final_sample

    def sample(self, batch_size=10, sequence_length=16, random=True, idx_list = None, tokenize=False, padding=True,  split=None)->dict:
        
        if idx_list != None:
            assert isinstance(idx_list, list)
            batch_size = len(idx_list)
            samples =  [self.__getitem__(idx=idx_list[i] ,split=split) for i in range(batch_size)]

        elif idx_list == None:
            samples =  [self.__getitem__(idx=None if random else i, split=split) for i in range(batch_size)]
        else:
            raise NotImplementedError(type(idx_list))

        sample_dict = {'text': samples}
        if tokenize:
            sample_dict['input_ids'] = self.tokenizer(sample_dict['text'], padding=padding,  max_length=sequence_length, truncation=True, return_tensors='pt')['input_ids']
            
        return sample_dict
    
    def resolve_device(self, device=None):
        if device == None:
            device = self.device
        return device

    classmethod
    def test_model_sample(cls):
        self = cls()
        batch_size=12
        seqeunce_length = 256
        x = self.sample(batch_size=batch_size,seqeunce_length=seqeunce_length )
        assert x.shape[0] == batch_size
        assert x.shape[1] == seqeunce_length


    def list_datasets(self,return_type = 'dict', filter_fn=None, *args, **kwargs):
        datasets = self.hf_api.list_datasets(*args,**kwargs)
        filter_fn = self.resolve_filter_fn(filter_fn=filter_fn)
        if return_type in 'dict':
            datasets = list(map(lambda x: x.__dict__, datasets))
            if filter_fn != None and callable(filter_fn):
                datasets = list(filter(filter_fn, datasets))
        elif return_type in ['pandas', 'pd']:
            datasets = list(map(lambda x: x.__dict__, datasets))
            df = pd.DataFrame(datasets)
            df['num_tags'] = df['tags'].apply(len)
            df['tags'] = df['tags'].apply(lambda tags: {tag.split(':')[0]:tag.split(':')[1] for tag in tags  }).tolist()
            for tag_field in ['task_categories']:
                df[tag_field] = df['tags'].apply(lambda tag:tag.get(tag_field) )
            df['size_categories'] = df['tags'].apply(lambda t: t.get('size_categories'))
            df = df.sort_values('downloads', ascending=False)
            if filter_fn != None and callable(filter_fn):
                df = self.filter_df(df=df, fn=filter_fn)
            return df
        else:
            raise NotImplementedError

    
        return datasets

    @property
    def task_categories(self):
        return list(self.datasets['task_categories'].unique())
    @property
    def pipeline_tags(self): 
        df = self.list_models(return_type='pandas')
        return df['pipeline_tag'].unique()
    @property
    def pipeline_tags_count(self):
        count_dict = dict(self.models_df['pipeline_tag'].value_counts())
        return {k:int(v) for k,v in count_dict.items()}

    @staticmethod
    def resolve_filter_fn(filter_fn):
        if filter_fn != None:
            if callable(filter_fn):
                fn = filter_fn

            if isinstance(filter_fn, str):
                filter_fn = eval(f'lambda r : {filter_fn}')
        
            assert(callable(filter_fn))
        return filter_fn
    @property
    def models(self):
        df = pd.DataFrame(self.list_models(return_type='dict'))
        return df
    @property
    def datasets(self):
        df = pd.DataFrame(self.list_datasets(return_type='dict'))
        return df



    def list_models(self,return_type = 'pandas',filter_fn=None, *args, **kwargs):
        models = self.hf_api.list_models(*args,**kwargs)
       
        filter_fn = self.resolve_filter_fn(filter_fn=filter_fn)


        if return_type in 'dict':
            models = list(map(lambda x: x.__dict__, models))
            if filter_fn != None and callable(filter_fn):
                models = list(filter(filter_fn, models))

        elif return_type in ['pandas', 'pd']:

            models = list(map(lambda x: x.__dict__, models))
            models = pd.DataFrame(models)
            if filter_fn != None and callable(filter_fn):
                models = self.filter_df(df=models, fn=filter_fn)

        else:
            raise NotImplementedError

        return models


    @property
    def task_categories(self):
        return list(self.datasets['task_categories'].unique())
    @property
    def pipeline_tags(self): 
        df = self.list_models(return_type='pandas')
        return df['pipeline_tag'].unique()



    def dataset_tags(self, limit=10, **kwargs):
        df = self.list_datasets(limit=limit,return_type='pandas', **kwargs)
        tag_dict_list = df['tags'].apply(lambda tags: {tag.split(':')[0]:tag.split(':')[1] for tag in tags  }).tolist()
        tags_df =  pd.DataFrame(tag_dict_list)
        df = df.drop(columns=['tags'])
        return pd.concat([df, tags_df], axis=1)

    @staticmethod
    def filter_df(df, fn):
        indices =  df.apply(fn, axis=1)
        return df[indices]


    def list_datasets(self, *args, **kwargs):
        df = self.hf_api.list_datasets( *args, **kwargs)
        return df    


    @staticmethod
    def load_dataset_builder( path:str=None, factory_module_path:str=None):
        if factory_module_path == None:
            assert isinstance(path, str)
            factory_module = datasets.load.dataset_module_factory(path)
            factory_module_path = factory_module.module_path

        dataset_builder = datasets.load.import_main_class(factory_module_path)
        return dataset_builder

    @staticmethod
    def load_dataset_factory( path:str):
        return datasets.load.dataset_module_factory(path)

    @property
    def dataset_factory(self):
        placeholder_name = '_dataset_factory'
        if not hasattr(self, placeholder_name):
            setattr(self, placeholder_name,self.load_dataset_factory(self.path))
        return getattr(self, placeholder_name)

    @property
    def dataset_builder(self):
        placeholder_name = '_dataset_builder'
        if not hasattr(self, placeholder_name):
            setattr(self, placeholder_name,self.load_dataset_builder(self.path))
        return getattr(self, placeholder_name)

    @property
    def path(self):
        return self.config['path']

    @path.setter
    def path(self, value):
        self.config['path'] = value

    @property
    def text_field(self):
        return self.config['text_field']

    @text_field.setter
    def text_field(self, value):
        self.config['text_field'] = value

    @property
    def name(self):
        if 'name' in self.config:
            return self.config['name']
        else:
            self.config['name'] = self.configs[0]

    @name.setter
    def name(self, value):
        self.config['name'] = value


    def list_configs(self):
        return self.config_map

    @property
    def configs(self):
        return list(self.config_map.keys())


    @property
    def config_map(self):

        configs = [config.__dict__ for config in self.dataset_builder.BUILDER_CONFIGS]

        if len(configs) == 0:
            configs =  [self.dataset_builder('default').info.__dict__]
            configs[0]['name'] = 'default'

        config_map = {config['name']: config for config in configs}
            

        return config_map
    



if __name__ == '__main__':
    module = DatasetModule( split='test')

    st.write(module.sample())
    