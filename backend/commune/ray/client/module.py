
# Create Ocean instance
import streamlit as st
import os, sys
sys.path.append(os.getenv('PWD'))
from commune import BaseModule
from functools import partial
import ray


class ClientModule(BaseModule):

    default_config_path = 'ray.client.module'
    server_module = None
    def __init__(self, config=None, **kwargs):
        BaseModule.__init__(self, config=config)
        self.config['server'] = kwargs.get('server', self.config.get('server'))
        self.server_module =self.get_actor(self.config['server'])
        self.parse()

    def submit(fn, fn_kwargs={}, fn_args=[], *args, **kwargs):
        ray_fn = getattr(self, fn)(*fn_args, **fn_kwargs)

    def submit_batch(fn, batch_kwargs=[], batch_args=[], *args, **kwargs):
        ray_get = kwargs.get('ray_get', True)
        ray_wait = kwargs.get('ray_wait', False)
        obj_id_batch = [getattr(self, fn)(*fn_args, **fn_kwargs) for fn_args, fn_kwargs in zip(batch_args, batch_kwargs)]
        if ray_get:
            return ray.get(obj_id_batch)
        elif ray_wait:
            return ray.wait(obj_id_batch)
    
    # def __getattribute__(self, item):
    #         # Calling the super class to avoid recursion
    #         server_module = BaseModule.__getattribute__(self,  'server_module')
    #         if server_module == None:
    #             return BaseModule.__getattribute__(self, item)
    #         return ray.get(server_module.getattr.remote(item))
    # def __setattr__(self, name, value):
    #         # Calling the super class to avoid recursion
    #         server_module = BaseModule.__getattribute__(self, 'server_module')
    #         if server_module == None:
    #             return BaseModule.__setattr__(self, name, value)
    #         return ray.get(server_module.getattr.remote(name, value))


    def parse(self):
        self.fn_signature_map = {}
        fn_ray_method_signatures = self.server_module._ray_method_signatures
        for fn_key in fn_ray_method_signatures:

            def fn(self, fn_key,server, *args, **kwargs):
                
                ray_get = kwargs.pop('ray_get', True)
                is_batched = any([ k in kwargs for k in ['batch_kwargs', 'batch_args']]) 

                batch_kwargs = kwargs.pop('batch_kwargs',  [kwargs])
                batch_args = kwargs.pop('batch_args', [args])

                ray_fn = getattr(server, fn_key)

                object_ids =[ray_fn.remote(*args, **kwargs) for b_args,b_kwargs in zip(batch_args, batch_kwargs)]
                

   
                if ray_get == True:
                    output_objects =  ray.get(object_ids)

                else:
                    output_objects =  object_ids

                if is_batched:
                    return output_objects
                else:
                    assert len(output_objects) == 1
                    return output_objects[0]


            self.fn_signature_map[fn_key] = fn_ray_method_signatures
            setattr(self, fn_key, partial(fn, self, fn_key, self.server_module))
        
        
if __name__ == '__main__':
    module = ClientModule.deploy(actor=True)
    # st.write(module.get_functions(module))


    