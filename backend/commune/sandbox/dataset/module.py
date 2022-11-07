##################
##### Import #####
##################
import torch
import concurrent.futures
import time
import psutil
import sys
import random
import argparse
from tqdm import tqdm
import bittensor
import glob
import queue
import streamlit as st
import numpy as np
import asyncio
import aiohttp
import json
import os
import nest_asyncio
from commune.threading.thread_manager import ThreadManager
from fsspec.asyn import AsyncFileSystem, sync, sync_wrapper
from bittensor._dataset.thread_queue import ThreadQueue
# from commune.sandbox.dataset.thread_queue import ThreadQueue

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
nest_asyncio.apply()

##########################
##### Get args ###########
##########################
from typing import *

class Dataset():
    """ Implementation for the dataset class, which handles dataloading from ipfs
    """

    ipfs_url = 'http://global.ipfs.opentensor.ai/api/v0'
    dataset_dir = 'http://global.ipfs.opentensor.ai/api/v0/cat' 
    text_dir = 'http://global.ipfs.opentensor.ai/api/v0/object/get'
    mountain_hash = 'QmSdDg6V9dgpdAFtActs75Qfc36qJtm9y8a7yrQ1rHm7ZX'
    

    def __init__(self, 
                loop:'asyncio.loop'=None, 
                tokenizer:'bittensor.tokenizer'=None, 
                datasets: List[str]=['ArXiv'], 
                run_generator=True,
                buffer_size:int=100):


        """
        Args:
            loop (asyncio.loop):
                The asyncio loop, defaults to default event loop
            
            tokenizer (bittensor.tokenizer):
                Tokenizer, defaults to bittensor.tokenizer
            
            datasets (List[str]):
                The list of dataset names to laod
            
            run_generator (bool): 
                Run the generator
            
            buffer_size (int):
                The size of the buffer for the generator.

        """
        
        # set the loop
        self.set_event_loop(loop=loop)
        st.write(self.loop.__str__)
        # set tokenizer (default is)
        self.set_tokenizer(tokenizer=tokenizer)


        # if datasets is None then refer to all of the availabe datasets 
        if datasets == None:
            datasets = self.available_datasets
        self.datasets = datasets


        # we need to build the dataset or load existing text file hashes
        # notice the heirarchy of ipfs hashes is DATASET -> FOLDER -> TEXT HASH, 
        # we want to flatten each dataset FOLDER -> TEXT HASH into FOLDER*TEXT
        self.build_datasets(datasets=self.datasets, load=True, save=False)


        self.data_queue = queue.Queue(buffer_size)

        # this runs the a thread that has its own asyncio loop. 
        # The loop is passed into nested async functions to use loo.run_until_complete function
        if run_generator:
            # the thread manager is used for running a background thread
            self.thread_manager = ThreadManager()
            # start the genrator
            self.thread_manager.submit(fn=self.sample_generator, kwargs=dict(queue=self.data_queue, loop=asyncio.new_event_loop()))
        else:
            # if the generator is not specified, it loops across the sample hashes
            self.sample_generator(queue=self.data_queue)

    def sample_generator(self, 
                        queue:queue.Queue, 
                        batch_size:int=8, 
                        sequence_length:int=128, 
                        loop:'asyncio.loop'=None, 
                        return_json:bool=False):

        """ Sample generator on seperate thread with its own asyncio loop for generating
            background samples while the user fetches them in the foreground.
        Args:
            queue (queue.Queue):
                Queue for feeding the samples through for __getitem__ to fetch.
            batch_size (int):
                Batch size of the samples.
            sequence_length (int):
                Sequence Length of the samples.
            loop:'asyncio.loop'=None, 
                        return_json:bool=False

        Returns: None
        """

        
        # this is for starting a new thread
        # the loop needs to be set within the new thread
        if loop != None:
            asyncio.set_event_loop(loop)


        # chunk the text hashes into batch_sie chunks
        text_hash_batch_list = self.chunk(self.all_text_hashes,
                                chunk_size=batch_size,
                                append_remainder=False,
                                distribute_remainder=False,
                                num_chunks= None)


        # run through each chunk, then tokenize it,
        for text_hash_batch in text_hash_batch_list:

            
            raw_text = self.async_run(self.get_text(text_hash_batch), loop=loop)
            raw_text = list(map(lambda x: ' '.join(str(x).split()[:200]), raw_text))
            

            # skip queue if it is full
            if not queue.full():
                tokenized_dict = self.tokenizer(raw_text, padding=True)
                output_dict = {k:torch.tensor(v)[:,:sequence_length] for k, v in tokenized_dict.items()}
                
                if return_json:
                    output_dict = {k:v.to_list() for k,v in output_dict.items()}

                queue.put(output_dict)

                
    def build_datasets(self, datasets:List[str], save:bool=False, load:bool=True, loop:'asyncio.loop'=None):
        """ Building all of the datasets specified by getting each of their 
            text hashes from IPFS or local
        Args:
            datasets (List[str]):
                Axon to serve.
            save (bool):
                Save the dataset hashes locally.
            load (bool):
                Load the dataset hashes locally
            loop (asyncio.Loop):
                Asyncio loop 

        Returns: None
        """

        all_text_hashes = []
        dataset_hash_map = {}

        if len(dataset_hash_map) == 0:
            tasks = []

            # gather dataset hashes async as their state is independent
            for dataset in datasets:
                tasks += [self.build_dataset(dataset=dataset, save=save, load=load, loop=loop)]

            dataset_hashes = self.async_run(asyncio.gather(*tasks), loop=loop)


            # create a hash map of dataset -> text hashes
            for k,v in zip(datasets, dataset_hashes):
                if len(v) > 0:
                    dataset_hash_map[k] = v


        # flatten the hash map into a 1D list
        self.dataset_hash_map = dataset_hash_map
        for  k,v in dataset_hash_map.items():
            all_text_hashes += v
        self.all_text_hashes = all_text_hashes


    # root dir for storing 
    root_dir = os.path.expanduser('~/./bittensor/dataset')

    async def async_save_json(self, 
                              path:str,
                              obj:Union[dict, list],
                              include_root:bool=True) -> str:
        """ 
        Async save of json for storing text hashes

        Args:
            path (List[str]):
                Axon to serve.
            obj (bool):
                The object to save locally
            include_root (bool):
                Include self.root_dir as the prefix.
                    - if True, ths meants shortens the batch and 
                    specializes it to be with respect to the dataset's 
                    root path which is in ./bittensor/dataset
            
        Returns: path (str)
            Path of the saved JSON
        """
        
        if include_root:
            path = os.path.join(self.root_dir, path)



        dir_path = os.path.dirname(path)

        # ensure the json is the prefix
        if path[-len('.json'):] != '.json':
            path += '.json'

        # ensure the directory exists, make otherwise
        if not os.path.isdir(dir_path):
            os.makedirs(dir_path)


        with open(path, 'w') as outfile:
            json.dump(obj, outfile)

        return path



    def save_json(self,loop:'asyncio.loop'=None, *args,**kwargs) -> str:
        '''
        Sync verson of async_save_json

        Args
            loop (asyncio.loop):
                The asyncio loop to be past, otherwise self.loop

        Returns (str) 

        '''
        return self.async_run(self.async_save_json(*args,**kwargs),loop=loop)


    async def async_load_json(self, path:str,include_root:bool=True, default:Union[list, dict]={}) -> Union[list, dict]:

        """ 
        Async save of json for storing text hashes

        Args:
            path (str):
                Path of the loaded json

            include_root (bool):
                Include self.root_dir as the prefix.
                    - if True, ths meants shortens the batch and 
                    specializes it to be with respect to the dataset's 
                    root path which is in ./bittensor/dataset
            
        Returns: path (str)
            Path of the saved JSON
        """
        
        
        if include_root:
            path = os.path.join(self.root_dir, path)


        # ensure extension
        dir_path = os.path.dirname(path)
        if path[-len('.json'):] != '.json':
            path += '.json'

        # ensure dictionary
        if not os.path.isdir(dir_path):
            os.makedirs(dir_path)


        # load default if file does not exist
        try:
            with open(path, 'r') as f:
                obj = json.load(f)
        except FileNotFoundError:
            obj = default


        if isinstance(obj, str):
            obj = json.loads(obj)


        return obj

    def load_json(self, loop:'asyncio.loop'=None, *args,**kwargs) -> Union[list, dict]:
        '''
        Sync verson of async_save_json

        Args
            loop (asyncio.loop):
                The asyncio loop to be past, otherwise self.loop

        Returns (dict, list) 

        '''
        return self.async_run(job=self.async_load_json(*args,**kwargs), loop=loop)

    
    async def build_dataset(self, dataset=None, num_folders=10, num_samples=100, save=False, load=True, loop=None):

        folder_hashes = (await self.get_folder_hashes(self.dataset2hash[dataset]))[:num_folders]
        random.shuffle(folder_hashes)

        loaded_text_hashes, new_text_hashes = [], []
        if load:
            loaded_text_hashes =  self.load_json(path=f'{dataset}/hashes', default=[], loop=loop)
            if len(loaded_text_hashes)>num_samples:
                return loaded_text_hashes[:num_samples]

        for f in folder_hashes:
            
            self.total = 0
            folder_text_hashes = await self.get_text_hashes(f)
            new_text_hashes += folder_text_hashes 
            

            if (len(new_text_hashes) + len(loaded_text_hashes)) >num_samples:
                break
                
        text_hashes = new_text_hashes + loaded_text_hashes
        self.save_json(path=f'{dataset}/hashes', obj=text_hashes, loop=loop)
        return text_hashes


    def __getitem__(self):
        '''
        Get the item of the queue (only use when sample_generator is running)
        '''

        return self.data_queue.get()
    
    async def get_dataset_hashes(self):
        mountain_meta = {'Name': 'mountain', 'Folder': 'meta_data', 'Hash': self.mountain_hash}
        response = await self.api_post( url=f'{self.ipfs_url}/object/get',  params={'arg': mountain_meta['Hash']}, return_json= True)
        response = response.get('Links', None)
        return response

    async def get_folder_hashes(self, 
                                file_meta:dict,
                                num_folders:int = 5) -> List[str]:
        '''
        Get the folder hashes from the dataset.

        Args:
            file_meta (dict):
                File meta contianing the hash and name of the link.
            num_folders (int):
                The number of folders to load at once
        Returns folder_hashes (List[str])
        
        '''
        links = (await self.get_links(file_meta))[:100]

        unfinished = [self.loop.create_task(self.api_post(self.ipfs_url+'/object/get', params={'arg':link['Hash']}, return_json=True)) for link in links]
        folder_hashes = []
        while len(unfinished)>0:
            finished, unfinished = await asyncio.wait(unfinished, return_when=asyncio.FIRST_COMPLETED)
            for res in await asyncio.gather(*finished):

                folder_hashes.extend(res.get('Links'))

        return folder_hashes

    async def get_text_hashes(self, file_meta:dict, num_hashes:int=50) -> List[str]:
        """
        Get text hashes from a folder

        Args:
            file_meta (dict):
                File meta contianing the hash and name of the link.
            num_hashes:
                The maximum number of hashes before stopping.
        
        Returns List[str]

        """

        try:
            data = await self.api_post(f'{self.ipfs_url}/cat', params={'arg':file_meta['Hash']}, return_json=False, num_chunks=10)
        except KeyError:
            return []
        decoded_hashes = []
        hashes = ['['+h + '}]'for h in data.decode().split('},')]
        for i in range(len(hashes)-1):
            try:
                decoded_hashes += [json.loads(hashes[i+1][1:-1])]
            except json.JSONDecodeError:
                pass

            if len(decoded_hashes) >= num_hashes:
                return decoded_hashes
            # hashes[i] =bytes('{'+ hashes[i+1] + '}')


    total = 0 
    async def get_text(self, file_meta, chunk_size=1024, num_chunks=2, loop=None):
        
        """
        Get text hashes from a folder

        Args:
            file_meta (dict):
                File meta contianing the hash and name of the link.
            num_hashes:
                The maximum number of hashes before stopping.
        
        Returns List[str]

        """

        
        if loop == None:
            loop = self.loop
        

        if isinstance(file_meta, dict):
            file_meta_list = [file_meta]
        elif isinstance(file_meta, list):
            file_meta_list = file_meta
        tasks = []
        def task_cb(context):
            self.total += len(context.result())

        for file_meta in file_meta_list:
            task = self.api_post(self.ipfs_url+'/cat', params={'arg':file_meta['Hash']},chunk_size=chunk_size, num_chunks=num_chunks )
            tasks.append(task)

        
        return await asyncio.gather(*tasks)


    async def get_links(self, file_meta:dict, **kwargs) -> List[dict]:
        '''
        Get Links from file_meta

        Args
            file_meta (dict): 
                Dictionary containing hash and name of root link
        
        Returns (List[dict])

        '''
        response = await self.api_post( url=f'{self.ipfs_url}/object/get',  params={'arg': file_meta['Hash']}, return_json= True)
        response_links = response.get('Links', [])
        return response_links


    async def api_post(self, 
                      url:str, 
                      return_json:bool = False, 
                      content_type:str=None, 
                      chunk_size:int=1024, 
                      num_chunks:int=None, 
                      **kwargs) -> 'aiohttp.Response':
        
        '''
        async api post

        Args:
            url (str):
                url of endpoint.
            return_json (bool): 
                Return repsonse as json.
            content_type (str):
                Content type of request.
            chunk_size (int):
                Chunk size of streaming endpoint.
            num_chunks (int):
                Number of chunks to stream.
        Returns (aiohttp.Response)
        '''
        headers = kwargs.pop('headers', {}) 
        params = kwargs.pop('params', kwargs)
        return_result = None


        # we need to  set the 
        timeout = aiohttp.ClientTimeout(sock_connect=10, sock_read=10)
        async with aiohttp.ClientSession( timeout=timeout) as session:
            async with session.post(url,params=params,headers=headers) as res:
                if return_json: 
                    return_result = await res.json(content_type=content_type)
                else:
                    return_result = res

                # if num_chunks != None
                if num_chunks:
                    return_result = b''
                    async for data in res.content.iter_chunked(chunk_size):
                        return_result += data
                        num_chunks-= 1
                        if num_chunks == 0:
                            break
        return return_result


    async def api_get(self, 
                      url:str,
                    return_json:bool = True,
                     content_type:str=None, 
                     chunk_size:int=1024, 
                     num_chunks:int=1,
                     **kwargs) -> 'aiohttp.Response':
        '''
        async api post

        Args:
            url (str):
                url of endpoint.
            return_json (bool): 
                Return repsonse as json.
            content_type (str):
                Content type of request.
            chunk_size (int):
                Chunk size of streaming endpoint.
            num_chunks (int):
                Number of chunks to stream.
        Returns (aiohttp.Response)
        '''
        headers = kwargs.pop('headers', {}) 
        params = kwargs.pop('params', kwargs)
        return_result = None
        async with aiohttp.ClientSession(loop=self.loop) as session:
            async with session.get(url,params=params,headers=headers) as res:
                if return_json: 
                    return_result = await res.json(content_type=content_type)
                else:
                    return_result = res

                if chunk_size:
                    return_result = b''
                    async for data in res.content.iter_chunked(chunk_size):
                        return_result += data
                        num_chunks-= 1
                        if num_chunks == 0:
                            break
        return return_result


    ##############
    #   ASYNCIO
    ##############
    @staticmethod
    def reset_event_loop(set_loop:bool=True) -> 'asyncio.loop':
        '''
        Reset the event loop

        Args:
            set_loop (bool):
                Set event loop if true.

        Returns (asyncio.loop)
        '''
        loop = asyncio.new_event_loop()
        if set_loop:
            asyncio.set_event_loop(loop)
        return loop

    def set_event_loop(self, loop:'asyncio.loop'=None)-> 'asynco.loop':
        '''
        Set the event loop.

        Args:
            loop (asyncio.loop):
                Event loop.

        Returns (asyncio.loop)
        '''
        
        if loop == None:
            loop = asyncio.get_event_loop()
        self.loop = loop
        return self.loop
         
    def async_run(self, job, loop=None): 
        '''
        Set the event loop.

        Args:
            job (asyncio.Task)
            loop (asyncio.loop):
                Event loop.

        '''
        
        if loop == None:
            loop = self.loop
        return loop.run_until_complete(job)


    @property
    def dataset2size(self) -> Dict:
        '''
        dataset to the number of hashes in the dataset
        '''
        return {k:v['Size'] for k,v in self.dataset2hash.items()}
    @property
    def available_datasets(self) -> List[str]:
        '''
        list of available datasets
        '''

        return list(self.dataset2hash.keys())
    @property
    def dataset2hash(self) -> Dict:
        '''
        Dictionary to hash
        '''
        return {v['Name'].replace('.txt', '') :v for v in self.dataset_hashes}
    

    @property
    def dataset_hashes(self) -> List[str]:
        '''
        Return the dataset hashes
        '''


        if not hasattr(self, '_dataset_hashes'):
            self._dataset_hashes = self.async_run(self.get_dataset_hashes())
        return self._dataset_hashes
    def set_tokenizer(self, tokenizer:bittensor.tokenizer=None) -> bittensor.tokenizer:
        '''
        Resolve the tokenizer
        '''
        if tokenizer == None:
            tokenizer = bittensor.tokenizer()
        
        self.tokenizer = tokenizer

    @staticmethod
    def chunk(sequence:list,
            chunk_size:str=None,
            append_remainder:bool=False,
            distribute_remainder:bool=True,
            num_chunks:int= None):

        '''
        Chunk a list into N chunks for batching

        Args:
            sequence (list):
                Size of the sequence Length
            chunk_size (str):
                Size of the chunk.
            append_remainder (bool):
                Append the remainder
            distribute_remainder (bool):
                Distribute the remainder as round robin
            num_chunks (int):
                The number of chunks.

        Returns (int)

        '''

        
        # Chunks of 1000 documents at a time.

        if chunk_size is None:
            assert (type(num_chunks) == int)
            chunk_size = len(sequence) // num_chunks

        if chunk_size >= len(sequence):
            return [sequence]
        remainder_chunk_len = len(sequence) % chunk_size
        remainder_chunk = sequence[:remainder_chunk_len]
        sequence = sequence[remainder_chunk_len:]
        sequence_chunks = [sequence[j:j + chunk_size] for j in range(0, len(sequence), chunk_size)]

        if append_remainder:
            # append the remainder to the sequence
            sequence_chunks.append(remainder_chunk)
        else:
            if distribute_remainder:
                # distributes teh remainder round robin to each of the chunks
                for i, remainder_val in enumerate(remainder_chunk):
                    chunk_idx = i % len(sequence_chunks)
                    sequence_chunks[chunk_idx].append(remainder_val)

        return sequence_chunks

    

if __name__ == '__main__':
    d = Dataset()
    for i in range(10):
        st.write(torch.where(torch.tensor(d.__getitem__()['attention_mask'])==0))