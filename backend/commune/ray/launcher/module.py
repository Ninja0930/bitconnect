
from copy import deepcopy
import sys
import datetime
import os
import asyncio
import multiprocessing
import torch
sys.path.append(os.environ['PWD'])
from commune.config import ConfigLoader
import ray
from ray.util.queue import Queue
import torch
from commune.utils import  (RunningMean,
                        chunk,
                        get_object,
                        even_number_split,
                        torch_batchdictlist2dict,
                        round_sig,
                        timer,
                        tensor_dict_shape,
                        nan_check,
                        dict_put, dict_has, dict_get, dict_hash
                        )

from commune import BaseModule


def cache():
    def wrapper_fn(self,*args, **kwargs):
        self.save_
    

class Launcher(BaseModule):


    default_config_path = "ray.launcher"

    def __inif__(self, config=None, **kwargs):
        BaseModule.__init__(self, config=config, **kwargs)
        self.actor_map = {}

        self.config['queue'] = {
            'in': 'launcher.in',
            'out': 'launcher.out'
        }


    default_max_actor_count = 10
    @property
    def max_actor_count(self):
        return self.config.get('max_actor_count', self.default_max_actor_count)

    def send_job(self, job_kwargs, block=False):
        self.queue.put(topic=self.config['queue']['in'], item=job_kwargs, block=block )
        

    def run_job(self, module, fn, kwargs={}, args=[], override={}):

        actor = self.add_actor(module=module,override=override)
        job_id = getattr(actor, fn).remote(*args,**kwargs)
        job_kwargs = {'actor_name':actor.actor_name,
                        'fn': fn,
                        'args': args,
                        'kwargs': kwargs}

        self.register_job(actor_name=actor.actor_name, job_id=job_id)
        # if cron:
        #     self.register_cron(name=cron['name'], interval=cron['interval'], 
        #                         job = job_kwargs )

        self.client.ray.queue.put(topic=self.config['queue']['out'],item=job_id)
        
        return job_id


    @property
    def resource_limit(self):
        return {'gpu': torch.cuda.device_count(), 
                'cpu': multiprocessing.cpu_count(),
                'memory_percent': 0.5}

    # def load_balance(self, proposed_actor = None):

    #     while self.actor_count >= self.max_actor_count:
    #         for actor_name in self.actor_names:
    #             self.remove_actor(actor_name)

    def resolve_module(self, module):

        return module

    def add_actor_replicas(self, module, replicas=1,*args, **kwargs):
        module_name_list = [f"{module}-{i}" for i in range(replicas)]
        for module_name in module_name_list:
            self.add_actor(module=module_name, *args, **kwargs)
    
    def launch(self, module:str, 
                    refresh:bool=False,
                    resources:dict = {'num_gpus':0, 'num_cpus': 1},
                    name:str=None,
                    max_concurrency:int=100,
                     **kwargs):
        actor = {}

        actor_class = self.resolve_actor_class(module)

        actor['name'] = name if isinstance(name, str) else module
        actor['max_concurrency'] = max_concurrency
        actor['refresh'] = refresh
        actor['resources'] = resources
        kwargs['actor'] = actor

        return actor_class.deploy(**kwargs)

    get_actor = add_actor = launch_actor = launch

    def get_actor_replicas(self, actor_name):
        return list(filter(lambda f: actor_name == self.actor_names[:len(actor_name)], self.actor_names))       

    get_replicas = get_actor_replicas

    def process(self, **kwargs):
        self.get_config()
        run_job_kwargs = self.client['ray'].queue.get(topic=self.config['queue']['in'], block=True )        
        # print(run_job_kwargs,'BRO')
        self.run_job(**run_job_kwargs)
        out_item = ray.get(self.get_jobs('finished'))




    @property
    def actor_map(self):
        return BaseModule.actor_map()
    
    @property
    def actor_names(self):
        return list(self.actor_map.keys())

    @property
    def actors(self):
        return [ray.get_actor(name) for name in self.actor_names]

    @property
    def actor_count(self):
        return len(self.list_actors()) 

    def remove_actor(self,actor_name,timeout=10):
        '''
        params:
            actor: name of actor or handle
        '''
        if self.actor_exists(actor_name):
            self.kill_actor(actor_name)
            return actor_name

        return None

    @staticmethod
    def st_test():

        import streamlit as st
        module = Launcher.deploy(actor=False, wrap=True)
        st.write(module.actor_map)
    @property
    def available_modules(self):
        return list(self.simple2module.keys())

    module_list = available_modules
    

  
    rm = remove = remove_actor



    def remove_all_actors(self):
        for actor in self.actor_names:
            self.remove_actor(actor)

    rm_all = remove_all = remove_all_actors




if __name__=="__main__":


    Launcher.st_test()