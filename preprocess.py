#!/usr/bin/env python
# coding=utf-8

import pandas as pd
import numpy as np
import json
import csv
import os
import pickle
import re
import sys
import logging
import lightgbm as lgb
from collections import Counter, defaultdict
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_score, recall_score, roc_auc_score

from utils import base_train


np.set_printoptions(formatter={'float': lambda x: "{0:0.3f}".format(x)})
logging.getLogger().setLevel(logging.INFO)

def reduce_mem_usage(df, verbose=True):
    numerics = ['int16', 'int32', 'int64', 'float16', 'float32', 'float64']
    start_mem = df.memory_usage().sum() / 1024 ** 2
    for col in df.columns:
        col_type = df[col].dtypes
        if col_type in numerics:
            c_min = df[col].min()
            c_max = df[col].max()
            if str(col_type)[:3] == 'int':
                if c_min > np.iinfo(np.int8).min and c_max < np.iinfo(np.int8).max:
                    df[col] = df[col].astype(np.int8)
                elif c_min > np.iinfo(np.int16).min and c_max < np.iinfo(np.int16).max:
                    df[col] = df[col].astype(np.int16)
                elif c_min > np.iinfo(np.int32).min and c_max < np.iinfo(np.int32).max:
                    df[col] = df[col].astype(np.int32)
                elif c_min > np.iinfo(np.int64).min and c_max < np.iinfo(np.int64).max:
                    df[col] = df[col].astype(np.int64)
            else:
                if c_min > np.finfo(np.float16).min and c_max < np.finfo(np.float16).max:
                    df[col] = df[col].astype(np.float16)
                elif c_min > np.finfo(np.float32).min and c_max < np.finfo(np.float32).max:
                    df[col] = df[col].astype(np.float32)
                else:
                    df[col] = df[col].astype(np.float64)
    end_mem = df.memory_usage().sum() / 1024 ** 2
    if verbose: print('Mem. usage decreased to {:5.2f} Mb ({:.1f}% reduction)'.format(end_mem, 100 * (
                start_mem - end_mem) / start_mem))
    return df

'''

统计特征：
user纬度：点击广告总次数，creative_id个数，

'''


def agg_features(df_click_log, groupby_cols, stat_col, aggfunc):
    if type(groupby_cols) == str:
        groupby_cols = [groupby_cols]
    data = df_click_log[groupby_cols + [stat_col]]
    if aggfunc == "size":
        tmp = pd.DataFrame(data.groupby(groupby_cols).size()).reset_index()
    elif aggfunc == "count":
        tmp = pd.DataFrame(data.groupby(groupby_cols)[stat_col].count()).reset_index()
    elif aggfunc == "mean":
        tmp = pd.DataFrame(data.groupby(groupby_cols)[stat_col].mean()).reset_index()
    elif aggfunc == "unique":
        tmp = pd.DataFrame(data.groupby(groupby_cols)[stat_col].nunique()).reset_index()
    elif aggfunc == "max":
        tmp = pd.DataFrame(data.groupby(groupby_cols)[stat_col].max()).reset_index()
    elif aggfunc == "min":
        tmp = pd.DataFrame(data.groupby(groupby_cols)[stat_col].min()).reset_index()
    elif aggfunc == "sum":
        tmp = pd.DataFrame(data.groupby(groupby_cols)[stat_col].sum()).reset_index()
    elif aggfunc == "std":
        tmp = pd.DataFrame(data.groupby(groupby_cols)[stat_col].std()).reset_index()
    elif aggfunc == "median":
        tmp = pd.DataFrame(data.groupby(groupby_cols)[stat_col].median()).reset_index()
    elif aggfunc == "skew":
        tmp = pd.DataFrame(data.groupby(groupby_cols)[stat_col].skew()).reset_index()
    elif aggfunc == "unique_mean":
        group = data.groupby(groupby_cols)
        group = group.apply(lambda x: np.mean(list(Counter(list(x[stat_col])).values())))
        tmp = pd.DataFrame(group.reset_index())
    elif aggfunc == "unique_var":
        group = data.groupby(groupby_cols)
        group = group.apply(lambda x: np.var(list(Counter(list(x[stat_col])).values())))
        tmp = pd.DataFrame(group.reset_index())
    else:
        raise Exception("aggfunc error")
    feat_name = '_'.join(groupby_cols) + "_" + stat_col + "_" + aggfunc
    tmp.columns = groupby_cols + [feat_name]
    print(feat_name)
    return tmp
    # try:
    #     del df_click_log[feat_name]
    # except:
    #     pass
    # df_click_log = df_click_log.merge(tmp, how='left', on=groupby_cols)
    # return df_click_log


def get_features(df_click_log):
    paras = [
        (['user_id'], 'creative_id', 'unique'),
        (['user_id'], 'creative_id', 'count'),
        (['user_id'], 'ad_id', 'unique'),
        (['user_id'], 'ad_id', 'count'),
        (['user_id'], 'product_id', 'unique'),
        (['user_id'], 'product_id', 'count'),
        (['user_id'], 'product_category', 'unique'),
        (['user_id'], 'product_category', 'count'),
        (['user_id'], 'advertiser_id', 'unique'),
        (['user_id'], 'advertiser_id', 'count'),
        (['user_id'], 'industry', 'unique'),
        (['user_id'], 'industry', 'count')
    ]
    df_tmp = pd.DataFrame()
    for groupby_cols, stat_col, aggfunc in paras:
        tmp = agg_features(df_click_log, groupby_cols, stat_col, aggfunc)
        df_tmp = df_tmp.merge(tmp, how='left', on='user_id') if not df_tmp.empty else tmp
    return df_tmp


def main(args):
    txdir = "E:/ML/"
    df_ad = pd.read_csv(txdir + "train_preliminary/ad.csv")
    df_ad = reduce_mem_usage(df_ad)
    df_click_log = pd.read_csv(txdir + "train_preliminary/click_log.csv")
    df_click_log = reduce_mem_usage(df_click_log)
    df_user = pd.read_csv(txdir + "train_preliminary/user.csv")
    df_user = reduce_mem_usage(df_user)

    df_ad.loc[df_ad['product_id'] == '\\N', 'product_id'] = 0
    df_ad.loc[df_ad['industry'] == '\\N', 'industry'] = 0
    df_user['gender'] = df_user['gender'] - 1

    df_train, df_dev = train_test_split(df_user, test_size=0.2, random_state=2020)
    
    df_click_log=df_click_log.merge(df_ad, how="left", on="creative_id", )
    
    df_feat = get_features(df_click_log)

    df_train = df_train.merge(df_feat, how='left', on='user_id')
    df_dev = df_dev.merge(df_feat, how='left', on='user_id')
    y_train = df_train['gender']
    x_train = df_train.drop(['gender', 'age', 'user_id'], axis=1)
    y_dev = df_dev['gender']
    x_dev = df_dev.drop(['gender', 'age', 'user_id'], axis=1)

    gbm_gender = base_train(x_train, y_train, x_dev, y_dev, job='classification')

    y_train = df_train['age']
    x_train = df_train.drop(['gender', 'age', 'user_id'], axis=1)
    y_dev = df_dev['age']
    x_dev = df_dev.drop(['gender', 'age', 'user_id'], axis=1)
    gbm_age = base_train(x_train, y_train, x_dev, y_dev, job='regression')

    # 预测
    df_ad_test = pd.read_csv(txdir + "test/ad.csv")
    df_click_log_test = pd.read_csv(txdir + "test/click_log.csv")
    df_ad_test.loc[df_ad_test['product_id'] == '\\N', 'product_id'] = 0
    df_ad_test.loc[df_ad_test['industry'] == '\\N', 'industry'] = 0
    df_click_log_test = df_click_log_test.merge(df_ad_test, how='left')

    df_feat_test = get_features(df_click_log_test)
    df_res = df_feat_test[['user_id']]
    df_test = df_feat_test.drop(['user_id'], axis=1)
    df_res['predicted_age'] = gbm_age.predict(df_test)
    df_res['predicted_gender'] = gbm_gender.predict(df_test)
    df_res.loc[df_res['predicted_gender'] >= 0.5, 'predicted_gender'] = 2
    df_res.loc[df_res['predicted_gender'] < 0.5, 'predicted_gender'] = 1
    df_res.to_csv("submission.csv", index=False)


if __name__ == '__main__':
    args = None
    main(args)
