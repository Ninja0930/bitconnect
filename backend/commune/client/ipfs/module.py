import io
import time
import weakref
import copy
import asyncio
import aiohttp
from fsspec.asyn import _run_coros_in_chunks
from fsspec.utils import is_exception
from fsspec.callbacks import _DEFAULT_CALLBACK
from glob import has_magic
import json
from copy import deepcopy
from fsspec.asyn import AsyncFileSystem, sync, sync_wrapper
from ipfshttpclient.multipart import stream_directory, stream_files #needed to prepare files/directory to be sent through http
import os
from fsspec.exceptions import FSTimeoutError
from fsspec.implementations.local import LocalFileSystem
from fsspec.spec import AbstractBufferedFile
from fsspec.utils import is_exception, other_paths
import streamlit as st
import logging
from typing import *
from fsspec.asyn import AsyncFileSystem, sync, sync_wrapper
logger = logging.getLogger("ipfsspec")


class RequestsTooQuick(OSError):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after

DEFAULT_GATEWAY = None

import requests
from requests.exceptions import HTTPError
IPFSHTTP_LOCAL_HOST = 'ipfs'
from commune.client.local import LocalModule
from ipfshttpclient.multipart import stream_files, stream_directory
class IPFSClient:

    data_dir = '/tmp/ipfs_client'

    def __init__(self,
                ipfs_urls = {'get': f'http://{IPFSHTTP_LOCAL_HOST}:8080', 
                             'post': f'http://{IPFSHTTP_LOCAL_HOST}:5001'},
                loop=None,
                client_kwargs={}):

        self.ipfs_url = ipfs_urls
        self.local = LocalModule()
        self.path2hash = asyncio.run(self.load_path2hash())
        self.loop = asyncio.set_event_loop(asyncio.new_event_loop())

    def __del__(self):
        self.close_session(loop=self.loop, session=self._session)

    def ukey(self, path):
        """returns the CID, which is by definition an unchanging identitifer"""
        return self.info(path)["CID"]

    async def api_post(self, 
                      endpoint:str, 
                      params:dict = {} ,
                      headers:dict={},
                      data={},
                      return_json:bool = True, 
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


        url = os.path.join(self.ipfs_url['post'],'api/v0', endpoint)


        return_result = None
        # we need to  set the 
        timeout = aiohttp.ClientTimeout(sock_connect=10, sock_read=10)
        async with aiohttp.ClientSession( timeout=timeout) as session:
            async with session.post(url,params=params,headers=headers, data=data) as res:
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
                      endpoint:str,
                     return_json:bool = True,
                     content_type:str=None, 
                     chunk_size:int=1024, 
                     num_chunks:int=1,
                     params: dict={},
                     headers: dict={},
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

        url = os.path.join(self.ipfs_url['get'],'api/v0', endpoint)
    
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


    async def cid_head(self, session, path, headers, **kwargs):
        return await self._cid_req(session.head, path, headers=headers, **kwargs)

    async def cid_get(self, session, path,  **kwargs):
        return await self._cid_req(session.get, path, headers=headers, **kwargs)

    async def version(self, session):
        res = await self.api_get( endpoint="version" )
        res.raise_for_status()
        return await res.json()


    async def save_links(self, session, links):
        return await asyncio.gather(*[self.save_link(session=session, lpath=k,rpath=v)for k, v in links.items()])

    async def save_link(self, session, lpath,rpath):
        lpath_dir = os.path.dirname(lpath)
        
        if len(lpath.split('.')) < 2:
            if not os.path.isdir(lpath_dir): 
                os.mkdir(lpath_dir)
            await self.save_links(lpath=lpath, rpath= rpath)
        else:
            data = await self.cat_file(links[lpath]['Hash'])
            with open(k, 'wb') as f:
                f.write(data.encode('utf-8'))    



    async def cat(self, session, path):
        
        res = await self.api_get(endpoint='cat', arg=path)

        async with res:
            self._raise_not_found_for_status(res, path)
            if res.status != 200:
                raise FileNotFoundError(path)
            return await res.read()

    # async def add(self,
    #     path:Union[str, List[str]], # Path to the file/directory to be added to IPFS
    #     wrap_with_directory:bool=False, # True if path is a directory
    #     chunker:str='size-262144', # Chunking algorithm, size-[bytes], rabin-[min]-[avg]-[max] or buzhash
    #     pin:bool=True, # Pin this object when adding
    #     hash_:str='sha2-256', # Hash function to use. Implies CIDv1 if not sha2-256
    #     progress:str='true', # Stream progress data
    #     silent:str='false', # Write no output
    #     cid_version:int=0, # CID version
    #     **kwargs,
    #     ):
    #     "add file/directory to ipfs"

    #     params = {}
    #     params['wrap-with-directory'] = 'true' if wrap_with_directory else 'false'
    #     params['chunker'] = chunker
    #     params['pin'] = 'true' if pin else 'false'
    #     params['hash'] = hash_
    #     params['progress'] = progress
    #     params['silent'] = silent
    #     params['cid-version'] = cid_version
    #     params.update(kwargs)
        
    #     chunk_size = int(chunker.split('-')[1])

    #     if os.path.isfile(path):
    #         data, headers = stream_files(path, chunk_size=chunk_size)
    #     elif os.path.isdir(path):
    #         data, headers = stream_directory(path, chunk_size=chunk_size, recursive=True)
    #     else:
            
    #     response = await self.api_post('add', 
    #                                 params=params, 
    #                                 data=data,
    #                                 headers=headers)

    #     return response

    async def pin(self, session, cid, recursive=False, progress=False, **kwargs):
        kwargs['params'] = kwargs.get('params', {})
        kwargs['params'] = dict(arg=cid, recursive= recursive,progress= progress)
        res = await self.api_post(endpoint='pin/add', arg=cid, recursive= recursive,  **kwargs)
        return bool(cid in pinned_cid_list)



    async def add(self,
            path,
            pin=True,
            chunker=262144 ):

        if os.path.isdir(path):
            file_paths = self.local.glob(path+'/**')
        elif os.path.isfile(path):
            file_paths = [file_path]
        
        file_paths = list(filter(os.path.isfile, file_paths))

        assert len(file_paths) > 0
    
        jobs = asyncio.gather(*[self.add_file(path=fp, pin=pin, chunker=chunker) for fp in file_paths])
        responses = await jobs
        path2hash =  dict(zip(file_paths,responses))
        self.path2hash.update(path2hash)

        await self.save_json('path2hash', path2hash)

        return dict(zip(file_paths,responses))


    async def rm(self, path):
        assert path in self.path2hash, f'{path} is not in {list(self.path2hash.keys())}'
        file_meta = self.path2hash[path]
        return  await self.pin_rm(cid=file_meta['Hash'])

    async def pin_ls(self,
        type_:str='all', # The type of pinned keys to list. Can be "direct", "indirect", "recursive", or "all"
        **kwargs,
    ):
        'List objects pinned to local storage.'    

        params = {}
        params['type'] = type_
        params.update(kwargs)
        return await self.api_post('pin/ls', params=params)


    async def pin_rm(self,
        cid:str, # Path to object(s) to be unpinned
        recursive:str='true', #  Recursively unpin the object linked to by the specified object(s)
        **kwargs,
    ):
        'List objects pinned to local storage.'    

        params = {}
        params['arg'] = cid
        params['recursive'] = recursive
        params.update(kwargs)

        response = self.api_post('pin/rm', params=params)

        if response.status_code == 200:
            return response, parse_response(response)

        else:
            raise HTTPError (parse_error_message(response))


    async def add_file(self,
        path,
        pin=False,
        chunker=262144, 
        wrap_with_directory=False,
    ):

        params = {}
        params['wrap-with-directory'] = 'true' if wrap_with_directory else 'false'
        params['chunker'] = f'size-{chunker}'
        params['pin'] = 'true' if pin else 'false'
        
        data, headers = stream_files(path, chunk_size=chunker)

        async def data_gen_wrapper(data):
            for d in data:
                yield d
        data = data_gen_wrapper(data=data)   
                                  
        res = await self.api_post(endpoint='add',  params=params, data=data, headers=headers)


        return res
        # return res
    

    async def dag_get(self, session,  **kwargs):
        kwargs['params'] = kwargs.get('params', {})
        kwargs['params'] = dict(arg=cid, recursive= recursive,progress= progress)
        res = await self.api_post(endpoint='dag/get', session=session , **kwargs)
        return bool(cid in pinned_cid_list)




    async def save_json(self, 
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
                Include self.data_dir as the prefix.
                    - if True, ths meants shortens the batch and 
                    specializes it to be with respect to the dataset's 
                    root path which is in ./bittensor/dataset
            
        Returns: 
            path (str)
                Path of the saved JSON
        """
        
        if include_root:
            path = os.path.join(self.data_dir, path)

        dir_path = os.path.dirname(path)

        # ensure the json is the prefix
        if path[-len('.json'):] != '.json':
            path += '.json'

        # ensure the directory exists, make otherwise
        if not os.path.isdir(dir_path):
            os.makedirs(dir_path)

        assert os.access( dir_path , os.W_OK ), f'dir_path:{dir_path} is not writable'
        with open(path, 'w') as outfile:
            json.dump(obj, outfile)

        return path


    def rm_json(self, path=None, recursive=True, **kwargs):
        path = os.path.join(self.data_dir, path)
        return os.remove(path)

    async def save_json(self, 
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
                Include self.data_dir as the prefix.
                    - if True, ths meants shortens the batch and 
                    specializes it to be with respect to the dataset's 
                    root path which is in ./bittensor/dataset
            
        Returns: 
            path (str)
                Path of the saved JSON
        """
        
        if include_root:
            path = os.path.join(self.data_dir, path)

        dir_path = os.path.dirname(path)

        # ensure the json is the prefix
        if path[-len('.json'):] != '.json':
            path += '.json'

        # ensure the directory exists, make otherwise
        if not os.path.isdir(dir_path):
            os.makedirs(dir_path)

        assert os.access( dir_path , os.W_OK ), f'dir_path:{dir_path} is not writable'
        with open(path, 'w') as outfile:
            json.dump(obj, outfile)

        return path



    async def load_json(self, path:str,include_root:bool=True, default:Union[list, dict]={}) -> Union[list, dict]:

        """ 
        Async save of json for storing text hashes
        Args:
            path (str):
                Path of the loaded json
            include_root (bool):
                Include self.data_dir as the prefix.
                    - if True, ths meants shortens the batch and 
                    specializes it to be with respect to the dataset's 
                    root path which is in ./bittensor/dataset
        Returns: 
            obj (str)
                Object of the saved JSON.
        """
        
        if include_root:
            path = os.path.join(self.data_dir, path)

        # Ensure extension.
        dir_path = os.path.dirname(path)
        if os.path.splitext(path)[-1] != '.json':
            path += '.json'

        # Ensure dictionary.
        if not os.path.isdir(dir_path):
            os.makedirs(dir_path)

        # Load default if file does not exist.
        try:
            with open(path, 'r') as f:
                obj = json.load(f)
        except FileNotFoundError:
            obj = default
        except json.JSONDecodeError:
            obj = default

        if isinstance(obj, str):
            obj = json.loads(obj)
        return obj

    async def save_path2hash(self):
        assert isinstance(value,dict)
        pinned_cids = (await self.pin_ls()).get('Keys', {}).keys()
        path2hash = {}
        for path, file_meta in self.path2hash.items():
            if file_meta['Hash'] in pinned_cids:
                path2hash[path] = file_meta

        await self.save_json('path2hash', path2hash )

    async def load_path2hash(self):
        loaded_path2hash  = await self.load_json('path2hash')
        pinned_cids = (await self.pin_ls()).get('Keys', {}).keys()
        path2hash = {}
        for path, file_meta in loaded_path2hash.items():
            if file_meta['Hash'] in pinned_cids:
                path2hash[path] = file_meta
        self.path2hash = path2hash
        return path2hash

    
    @property
    def hash2path(self):
        path2hash = asyncio.run(self.load_path2hash())
        return {file_meta['Hash']: path for path, file_meta in path2hash.items()}

if __name__ == '__main__':
    module = IPFSClient()
    files = module.local.ls(f'{os.getenv("PWD")}/commune/client/ipfs')
    # st.write(asyncio.run(module.add(path='commune/client/ipfs')))
    # st.write(asyncio.run(module.load_json('path2hash')))
    st.write(module.hash2path)
    st.write(module.path2hash)
    st.write(asyncio.run(module.load_json('path2hash')))
