import tensorflow as tf
import pandas as pd
import tensorflow_text as text
import wandb
from wandb.keras import WandbCallback
from ml_collections import config_dict
import argparse
import tensorflow_hub as hub
from official.nlp import optimization 
import torch
from numba import cuda
from GPUtil import showUtilization as gpu_usage
import os
# https://www.kaggle.com/getting-started/140636

default_cfg = config_dict.ConfigDict()

default_cfg.bs = 32
default_cfg.seed = 42
default_cfg.preprocessor = 'https://tfhub.dev/tensorflow/bert_en_uncased_preprocess/3'
default_cfg.encoder = 'https://tfhub.dev/tensorflow/small_bert/bert_en_uncased_L-2_H-128_A-2/2'
default_cfg.arch = 'roberta_L-12_H-768_A-12'
default_cfg.learning_rate = 3e-5
default_cfg.PROJECT_NAME = "banking_77"
default_cfg.JOB_TYPE = "hyperparameter_optimize"
default_cfg.ENTITY = "basha"
default_cfg.SPLIT_DATA = "preprocess"
default_cfg.epochs = 5

model_dict = {
    "bert_L-2_H-128_A-2": ["https://tfhub.dev/tensorflow/bert_en_uncased_preprocess/3" , "https://tfhub.dev/tensorflow/small_bert/bert_en_uncased_L-2_H-128_A-2/2"],
    "bert_L-12_H-768_A-12": ["https://tfhub.dev/tensorflow/bert_en_cased_preprocess/3" , "https://tfhub.dev/tensorflow/bert_en_cased_L-12_H-768_A-12/4"],
    "bert_L-8_H-512_A-8": ["https://tfhub.dev/tensorflow/bert_en_uncased_preprocess/3" , "https://tfhub.dev/tensorflow/small_bert/bert_en_uncased_L-8_H-512_A-8/2"],
    "bert_L-4_H-768_A-12": ["https://tfhub.dev/tensorflow/bert_en_uncased_preprocess/3" , "https://tfhub.dev/tensorflow/small_bert/bert_en_uncased_L-4_H-768_A-12/2"],
    "albert_en_large": ["http://tfhub.dev/tensorflow/albert_en_preprocess/3" , "https://tfhub.dev/tensorflow/albert_en_large/3"],
    "electra_large": ["https://tfhub.dev/tensorflow/bert_en_uncased_preprocess/3" , "https://tfhub.dev/google/electra_large/2"],
    "roberta_L-12_H-768_A-12": ["https://tfhub.dev/jeongukjae/roberta_en_cased_preprocess/1" , "https://tfhub.dev/jeongukjae/roberta_en_cased_L-12_H-768_A-12/1"]
}



def free_gpu_cache():
    print("Initial GPU Usage")
    gpu_usage()                             
    torch.cuda.empty_cache()

    cuda.select_device(0)
    cuda.close()
    cuda.select_device(0)
    
    print("GPU Usage after emptying the cache")
    gpu_usage()

free_gpu_cache() 

def set_model_url(model_name):
   return model_dict[model_name][0] , model_dict[model_name][1]


# optional
def parse_args():
    
    default_cfg.preprocessor , default_cfg.encoder = set_model_url(default_cfg.arch)
    "Overriding default argments"
    argparser = argparse.ArgumentParser(description='Process hyper-parameters')
    argparser.add_argument('--bs', type=int, default=default_cfg.bs, help='batch size')
    argparser.add_argument('--seed', type=int, default=default_cfg.seed, help='random seed')
    argparser.add_argument('--epochs', type=int, default=default_cfg.epochs, help='number of training epochs')
    argparser.add_argument('--learning_rate', type=float, default=default_cfg.learning_rate, help='learning rate')
    argparser.add_argument('--arch', type=str, default=default_cfg.arch, help='BERT type architecture')
    return argparser.parse_args()




def prepare_data(processed_data_at):

    # split = wandb.use_artifact(f'{processed_data_at}:latest')
    # split_dir = split.download()

    train_df = pd.read_csv(f'{processed_data_at}//train_split.csv')
    valid_df = pd.read_csv(f'{processed_data_at}//valid_split.csv')
    test_df = pd.read_csv(f'{processed_data_at}//test_split.csv')

    return train_df,valid_df,test_df

def load_data(train,valid,test,batchsize):
    AUTOTUNE = tf.data.AUTOTUNE

    train_ds = tf.data.Dataset.from_tensor_slices((train['text'],train['label'])).batch(batchsize).cache().prefetch(buffer_size=AUTOTUNE)
    valid_ds = tf.data.Dataset.from_tensor_slices((valid['text'],valid['label'])).batch(batchsize).cache().prefetch(buffer_size=AUTOTUNE)
    test_ds = tf.data.Dataset.from_tensor_slices((test['text'],test['label'])).batch(batchsize).cache().prefetch(buffer_size=AUTOTUNE)

    return train_ds,valid_ds,test_ds


def make_model(config):
    text_input = tf.keras.Input(shape=(),dtype=tf.string,name="text")
    preprocessor = hub.KerasLayer(config.preprocessor,name=f"{config.arch}_preprocessor")
    encoder_inputs = preprocessor(text_input)

    encoder = hub.KerasLayer(config.encoder,trainable=True,name=f'{config.arch}_encoder')
    outputs = encoder(encoder_inputs)

    pooled_output = outputs['pooled_output']

    net = tf.keras.layers.Dense(77,activation='softmax',name="classifier")(pooled_output) #predict 77 classes
    return tf.keras.Model(text_input, net)

def build_train(cfg):

    
    # with  wandb.init(project=cfg.PROJECT_NAME,job_type=cfg.JOB_TYPE,entity=cfg.ENTITY,config=cfg):
    train_df, valid_df, test_df = prepare_data(".//artifacts//preprocess-v0")
    train_ds, valid_ds, test_ds = load_data(train_df,valid_df,test_df,cfg.bs)

    del train_df, valid_df, test_df

    steps_per_epoch = tf.data.experimental.cardinality(train_ds).numpy()
    num_train_steps = steps_per_epoch * cfg.epochs
    num_warmup_steps = int(0.1*num_train_steps)

    print(f'steps_per_epoch=={steps_per_epoch} , num_train_steps=={num_train_steps} , num_warmup_steps=={num_warmup_steps}')
    model = make_model(cfg)
    init_lr = cfg.learning_rate
    optimizer = optimization.create_optimizer(init_lr=init_lr,
                                          num_train_steps=num_train_steps,
                                          num_warmup_steps=num_warmup_steps,
                                          optimizer_type='adamw')
        
    model.compile(optimizer=optimizer,
                         loss=tf.keras.losses.SparseCategoricalCrossentropy(),
                         metrics=['accuracy'])


    # model.fit(train_ds,validation_data=valid_ds,epochs=cfg.epochs,callbacks=[WandbCallback(save_model=False)])
    model.fit(train_ds,validation_data=valid_ds,epochs=cfg.epochs)

    loss , acccuracy = model.evaluate(test_ds)

    print({'test_loss': loss ,"test_accuracy": acccuracy})

if __name__ == "__main__":
    os.environ['TF_GPU_ALLOCATOR']='cuda_malloc_async'
    default_cfg.update(vars(parse_args()))
    free_gpu_cache()
    build_train(default_cfg)


