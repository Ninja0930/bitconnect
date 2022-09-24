
import streamlit as st
from random import shuffle, seed
from collections import defaultdict
import pandas as pd
import bittensor
import torch
from torch import nn
from tqdm.auto import tqdm
from torch.nn import CrossEntropyLoss
import torch.nn.functional as F

from commune.bittensor import BitModule
from commune import BaseModule

from commune.utils import *


import torch
from torch import nn
from sentence_transformers import SentenceTransformer



class RankingLoss(nn.Module):
    def __init__(self):
        super(RankingLoss, self).__init__()

    def forward(self, x, y):
        print(self)
        loss = torch.mean((x - y) ** 2)
        return loss


class RankingModel(nn.Module):
    def __init__(self, num_endpoints: int):

        super().__init__()
        self.num_endpoints = num_endpoints

        self.transformer = SentenceTransformer(
            "sentence-transformers/all-distilroberta-v1"
        )

        # TODO match embedding dim to transformer
        self.embeddings = torch.nn.Embedding(
            num_embeddings=num_endpoints,
            embedding_dim=self.transformer.get_sentence_embedding_dimension(),
        )

    def forward(self, sequence):

        seq_embeddings = torch.tensor(self.transformer.encode(sequence))

        # (num_receptors, dim)
        endpoint_embeddings = self.embeddings(torch.arange(0, self.num_endpoints))
        endpoint_embeddings = torch.nn.functional.normalize(endpoint_embeddings, p=2, dim=1)

        # (batch_size, num_endpoints)
        sims = torch.matmul(seq_embeddings, endpoint_embeddings.T)
        sims = (sims + 1) / 2  # bound from (0, 1)

        return sims



class BenchmarkModule(BitModule):
    __file__ = __file__
    default_config_path = 'bittensor.benchmark.module'
    def __init__(self, config=None, load_state=True, **kwargs):
        BitModule.__init__(self, config=config, **kwargs)
        if load_state:
            self.load_state()
    @property
    def debug(self):
        return self.config.get('debug', False)

    def load_state(self):
        if self.config.get('sync') == True:
            self.sync()
        self.load_dataset()
        self.load_tokenizer()
        self.load_model()
        self.load_optimizer()
        self.load_metric()
        self.load_receptor_pool()

    def load_dataset(self, **kwargs):
        dataset_kwargs = dict(path='bittensor.dataset', params=dict(block_size=128))
        dataset_kwargs.update(kwargs)
        dataset_kwargs.update(self.config.get('dataset'))
        dataset_class = self.import_object(dataset_kwargs['path'])
        self.dataset = dataset_class(**dataset_kwargs['params'])

    def load_tokenizer(self, **kwargs): 
        if isinstance(self.dataset, bittensor.dataset):
            self.tokenizer = self.dataset.tokenizer

        tokenizer_kwargs = dict(path='bittensor.tokenizer',
                            params=dict(version=bittensor.__version__))
        tokenizer_kwargs.update(kwargs)
        tokenizer_kwargs.update(self.config.get('tokenizer'))
        tokenizer_class = self.import_object(tokenizer_kwargs['path'])
        self.tokenizer = tokenizer_class(**tokenizer_kwargs['params'])

    def load_model(self):
        model_config = self.config['model']
        self.model = RankingModel(**model_config['params'])
        self.num_endpoints = self.model.num_endpoints
    
    def load_optimizer(self,**kwargs):
        optimizer_kwargs = dict(path='torch.optim.Adam', params=dict(lr=0.00032))
        optimizer_kwargs.update(kwargs)
        optimizer_kwargs.update(self.config.get('optimizer', {}))
        optim_class = self.import_object(optimizer_kwargs['path'])
        self.optimizer = optim_class(self.model.parameters(),**optimizer_kwargs['params'])


    def load_metric(self, **kwargs):
        metric_config = self.config['metric']
        self.metric = RankingLoss(**metric_config['params'])


    def restart_receptor_pool(self):
        del self.receptor_pool
        self.load_receptor_pool()


    def load_receptor_pool(self, **kwargs):

        receptor_kwargs = dict(max_worker_threads=64, max_active_receptors=512)
        receptor_kwargs.update(kwargs)
        receptor_kwargs.update(self.config.get('receptor_pool', {}))
        receptor_pool = self.get_object('bittensor.receptor.pool.module.ReceptorPoolModule')
        self.receptor_pool = receptor_pool(**receptor_kwargs,wallet=self.wallet)

    @staticmethod
    def causal_lm_loss(labels, logits):
        batch_size = logits.shape[0]
        loss_fct = CrossEntropyLoss()

        losses = []
        for batch in range(batch_size):
            shift_logits = logits[batch, :-1, :].contiguous()
            shift_labels = labels[batch, 1:].contiguous()
            loss = loss_fct(shift_logits.view(-1, 50258), shift_labels.view(-1))
            losses.append(loss)
        return torch.tensor(losses)

    @property
    def num_receptors(self):
        return self.num_endpoints

    def get_endpoints(self, num_endpoints=None, random_sample=True):
        if num_endpoints == None:
            num_endpoints =self.num_endpoints
        endpoints =self.graph.endpoint_objs

        if random_sample == True:
            endpoint_index_list = list(np.random.randint(0, num_endpoints, (10)))
            endpoints = [endpoints[idx] for idx in endpoint_index_list]
        else:
            endpoints = endpoints[:num_endpoints]
        return endpoints

    # def get_loss_fn(self):
    #     return nn.CrossEntropyLoss()
    
    @staticmethod
    def str2synapse(synapse:str):
        return getattr(bittensor.synapse, synapse)()
    @property
    def synapses(self):
        # default_synapses = ['bittensor.synapse.TextCausalLM']
        # synapse_class_strings = self.config.get('synapses', default_synapses)
        # return [self.import_module(s)() for s in synapse_class_strings]
        # return [bittensor.synapse.TextCausalLM()] 
        synsapses = list(map(self.str2synapse, self.config.get('synapses',['TextLastHiddenState'])) )
        return synsapses

    def predict(self,text=None, num_endpoints=10, timeout=1, synapses = None, return_type='df'):
        

        if synapses == None:
            synapses = self.synapses
        elif isinstance(synspses, str):
            synapses = [self.str2synapse(synapse)]
        elif isinstance(synapses, list):
            if isinstance(synapse[0], str):
                synapses = list(map(self.str2synapse, synapses))

        if text == None:
            text = self.raw_sample()
        endpoints = self.get_endpoints(num_endpoints=num_endpoints)
        if text == None:
            text='yo whadup fam'
        if isinstance(text, str):
            text = [text]
        inputs = torch.tensor(self.tokenizer(text=text, padding=True)['input_ids'])

        elasped_time = 0
        with Timer(text='Querying Endpoints: {t}', streamlit=True) as t:
            results = self.receptor_pool.forward(endpoints, synapses=self.synapses, inputs=[inputs] * len(endpoints), timeout=timeout)
            elasped_time = t.elapsed_time
        
        num_responses = len(results[1])

        if return_type in ['df']:
            df = []
            for i,e in enumerate(endpoints): 
                if i < num_responses:
                    row_dict = e.__dict__
                    row_dict['code'] = results[1][i][0]
                    row_dict['latency'] = results[2][i][0]
                    # row_dict['elapsed_time'] = elasped_time
                    row_dict['timeout'] = timeout
                    row_dict['return_endpoints'] = num_responses
                    row_dict['query_endpoints'] = num_endpoints
                    row_dict['output_size'] = sys.getsizeof(results[0][i])
                    row_dict['input_size'] = sys.getsizeof(inputs)


                    df.append(row_dict)
            
            df = pd.DataFrame(df)
            df = pd.merge(self.graph.to_dataframe(), df, on='uid')
        elif return_type in ['results', 'result']:

            return torch.cat([tensor[0] for tensor in results[0]], 0)
        
        return df


    def run_experiment(self,  trials=5, timeout_list = [1,2,5], num_endpoints_list=[10,20,50,100,500], path='experiments'):
        total_trials = len(timeout_list) * len(num_endpoints_list)* trials
        cnt = 0
        for timeout in timeout_list:
            for num_endpoints in num_endpoints_list:
                for i in range(trials):
                    cnt += 1 

                    text = self.raw_sample()
                    df = self.predict(text = text, num_endpoints=num_endpoints, timeout=timeout)
                    print(f'PROGRESS: {cnt}/{total_trials}')
                    self.put_json(f'{path}/num_endpoints_{num_endpoints}-timeout_{timeout}-trial_{i}', df)
                    self.restart_receptor_pool()
    
    
    def load_experiment(self, path='experiments'):
        df = []

        for p in self.ls_json(path):
            df.append(pd.DataFrame(self.get_json(p)))


        df = pd.concat(df)
        returnid2code = {k:f'{v}' for k,v in zip(bittensor.proto.ReturnCode.values(),bittensor.proto.ReturnCode.keys())}
        df['code'] = df['code'].map(returnid2code)
        return df

    def st_experiment(self, path='experiments'):

        df = self.load_experiment()
        with st.expander('dataframe', True):
            st.write(df.iloc[:50]) 
        with st.expander('Latency Histogram', True):
            fig =  module.plot.histogram(df, x='latency', color="code")
            fig.update_layout(legend={'traceorder':'normal'})
            st.write(fig)
        import plotly.express as px
        with st.expander('Return Code Pie Chart', True):
            code_count_dict = dict(df['code'].value_counts())
            codes_count_df =   pd.DataFrame({'codes': list(code_count_dict.keys()), 'values':  list(code_count_dict.values())})
            fig = px.pie(names=list(code_count_dict.keys()), 
                        values= list(code_count_dict.values()))
            st.write(codes_count_df)
            st.write(fig)

    def run(self):

        loss_fn = nn.CrossEntropyLoss()

        # https://github.com/huggingface/transformers/blob/v4.21.3/src/transformers/models/gptj/modeling_gptj.py#L847

        num_batches = 1
 
        for idx in range(num_batches):
            print("getting next batch of data")
            with Timer(text='Get Batch: {t}', streamlit=True) as t:
                inputs = next(self.dataset)
                st.write(inputs)


            with Timer(text='Tokenize: {t}', streamlit=True) as t:
                str_inputs = [self.tokenizer.decode(s) for s in inputs]

            st.write(str_inputs)
            print(f"Querying endpoints")
            # endpoints = self.get_endpoints()
            endpoints = self.get_endpoints()
    

            with Timer(text='Querying Endpoints: {t}', streamlit=True) as t:
                results = self.receptor_pool.forward(endpoints, synapses=self.synapses, inputs=[inputs] * len(endpoints), timeout=10)

            df = []
            for i,e in enumerate(endpoints): 
                row_dict = e.__dict__
                row_dict['code'] = results[1][i][0]
                row_dict['latency'] = results[2][i][0]
                df.append(row_dict)
            
            df = pd.DataFrame(df)
            st.write(df)

            break

            
            tensors = []
            for tensor in results[0]:
                tensors.append(tensor[0])
            


            codes = []
            codes_count = defaultdict(int)
            for code in results[1]:
                code = code[0]
                codes.append(code)
                codes_count[code] += 1
            for code in sorted(set(codes)):
                print(f"{code}: {codes_count[code]}")
        

            print("Calculating losses for each endpoint")
            all_losses = []
            for _, logits in tqdm(enumerate(tensors)):
                all_losses.append(self.causal_lm_loss(inputs, logits))

            all_losses_tensor = torch.vstack(all_losses).T  # (batch_size, num_endpoints)
            inv_loss_tensor = 1/all_losses_tensor


            print("Model forward")
            sims = self.model(str_inputs)

            print("model backwards")

            ideal_rankings = torch.argsort(all_losses_tensor, axis=1)
            model_rankings = torch.argsort(sims, axis=1)

            loss = loss_fn(sims, inv_loss_tensor)
            #ndcg = metrics.ndcg_score(ideal_rankings, model_rankings)
            print(f"step: {idx} | loss={loss.item():.3f}")

            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

    @property
    def coldkey_address(self):
        return self.wallet.coldkeypub.ss58_address

    def endpoints(self, return_type='list'):
        endpoints = self.graph.endpoint_objs
        if return_type =='list':
            endpoints = [e.__dict__ for e in endpoints]
        elif return_type == 'df':
            endpoints = pd.Dataframe([e.__dict__ for e in endpoints])
        return endpoints


    def my_endpoints(self, return_type = 'endpoint'):
        endpoints = self.graph.endpoint_objs
        
        endpoints = [e for e in endpoints if (e.coldkey == self.coldkey_address and e.ip != "0.0.0.0") ]
        st.write(self.coldkey_address)
        if return_type == 'endpoint':
            endpoints = endpoints
        elif return_type =='list':
            endpoints = [e.__dict__ for e in endpoints]
    
        elif return_type == 'df':
            endpoints = pd.Dataframe([e.__dict__ for e in endpoints])
        else:
            raise NotImplementedError

        return endpoints

    def raw_sample(self):
        text_field = self.config['dataset']['text_field']
        return self.dataset[random.randint(1,len(self.dataset))][text_field]

if __name__ == '__main__':
    module = BenchmarkModule(load_state=True)
    module.sync()
    # # graph_df = self.graph.to_dataframe()
    # # st.write(module.my_endpoints())
    # # st.write(module.endpoints())
    
    # # st.write(module.synapses)
    # st.write(module.raw_sample())

    # # # st.write('RUN')
    # df = module.predict(text=module.raw_sample(), num_endpoints=100, timeout=2)

    # module.put_json('df', df)
    # df = pd.DataFrame(module.get_json('df'))
    # st.write(module.plot.histogram(df, x='latency'))
    # st.write(datetime.utcnow().isoformat())
    df = module.predict(return_type='results')

    st.write(df.shape)


    

    # st.write(module.put_json('whadup/bro',['whadup']))
    # st.write(module.put_json('sub/bro',['whadup']))
    # st.write(module.get_json('whadup/bro'))
    # st.write(module.glob_json())
    # module.refresh_json()
    # st.write(module.glob_json())



    # fig = px.pie(df, values='pop', names='country', title='Population of European continent')
    # fig.show()