"""Dataset definition"""
from copy import copy, deepcopy
from torch.utils import data
from core.utils import concat_batches, to_tensor
from typing import Dict, Union, List
from torch.utils.data import Dataset, DataLoader
from pytorch_lightning import LightningDataModule
import torch
from transformers import AutoTokenizer
import pandas as pd
from loguru import logger
import random

concat_before_return = False


class NLIDataset(Dataset):
    def __init__(
        self,
        tokenizer: AutoTokenizer,
        config: Dict[str, Union[str, float, int]],
        train: bool = True
    ):
        super().__init__()
        self.tokenizer = tokenizer
        if train:
            self.data = pd.read_csv(config["train_data_path"])
            self.n_samples = config["train_n_samples"]
        else:
            self.data = pd.read_csv(config["val_data_path"])
            self.n_samples = config["val_n_samples"]
        self.n_samples = None if self.n_samples == -1 else self.n_samples
        
        self.bert_mnli = not config.get("dpsa", True)
        if not self.bert_mnli :
            self.data = self.data[self.data["label"] != "neutral"]
            self.label_factory = {"entailment": 1, "contradiction": -1, "neutral": 0}
        else :
            self.label_factory = {"entailment": 0, "contradiction": 1, "neutral": 2}
            
        self.max_length = config.get("max_length", 512)
        
        self.in_memory = config.get("in_memory", True) 
        if self.in_memory :
            logger.info("Get instances ...")
            self.data = [inst for inst in self.get_instances(self.data)]
            logger.info("Weigths %s"%str(self.weights))

    def sentence_and_cut(self, sentence):
        try:
            tokens = self.tokenizer.tokenize(sentence)
        except:
            print(sentence)
        tokens = tokens[: self.max_length]
        return self.tokenizer.convert_tokens_to_ids(tokens)

    def sentence_dont_cut(self, sentence):
        try:
            tokens = self.tokenizer.tokenize(sentence)
        except:
            print(sentence)
        return self.tokenizer.convert_tokens_to_ids(tokens)

    def to_tensor(self, sentences):
        return to_tensor(
            sentences,
            pad_index=self.tokenizer.pad_token_id,
            tokenize=self.sentence_and_cut,
            batch_first=True,
        )

    def __len__(
        self,
    ) -> int:
        return len(self.data)

    def __getitem__(self, idx) -> Dict[str, torch.Tensor]:
        if torch.is_tensor(idx):
            idx = idx.tolist()
        if self.in_memory :
            return self.data[idx]
        else :
            item = self.data.iloc[idx]
            label = item["label"]
            label = self.label_factory[label]
            if not self.bert_mnli :
                return self.to_tensor(item["sentence1"]), self.to_tensor(item["sentence2"]), float(label)
            else :
                x1, len1 = to_tensor(item["sentence1"], pad_index=self.tokenizer.pad_token_id, tokenize=self.sentence_dont_cut, batch_first=True)
                x2, len2 = to_tensor(item["sentence2"], pad_index=self.tokenizer.pad_token_id, tokenize=self.sentence_dont_cut, batch_first=True)
                output = concat_batches(x1, len1, x2, len2, self.tokenizer.cls_token_id, self.tokenizer.sep_token_id, self.tokenizer.pad_token_id)
                return output, float(self.label_factory[label])
            
    def get_instances(self, df, shuffle = False, group_by_size = False):
        self.shuffle = shuffle 
        self.group_by_size = group_by_size
        self.text_columns = ["sentence1", "sentence2"]       
        rows = df.iterrows()
        if self.shuffle :
            if self.n_samples or (not self.group_by_size and not self.n_samples) :
                rows = list(rows)
                random.shuffle(rows)
        if self.n_samples :
            rows = list(rows)[:self.n_samples]
        if self.group_by_size :
            sorted_criterion = lambda x : len(x[1][self.text_columns[0]].split()) #+ len(x[1][self.text_columns[1]].split())
            rows = sorted(rows, key = sorted_criterion, reverse=False)
        
        self.weights = {k : 0 for k in self.label_factory.keys()}
        
        for row in rows : 
            item = row[1]
            
            output1, output2 = item[self.text_columns[0]], item[self.text_columns[1]]
            # check NaN
            if (output1 != output1) or (output2 != output2) :
                logger.warning("NaN detected")
                continue
            
            label = item["label"]
            try :
                self.weights[label] += 1
            except KeyError :
                logger.warning("Unknow label : %s"%label)
                continue
            
            if not self.bert_mnli :
                output1 = self.to_tensor(output1)
                output2 = self.to_tensor(output2)
                yield output1, output2, float(self.label_factory[label])
            else :
                x1, len1 = to_tensor(output1, pad_index=self.tokenizer.pad_token_id, tokenize=self.sentence_dont_cut, batch_first=True)
                x2, len2 = to_tensor(output2, pad_index=self.tokenizer.pad_token_id, tokenize=self.sentence_dont_cut, batch_first=True)

                if len1 + 2 >= self.max_length : #or len1 + len2 + 3 >= self.max_length : 
                    logger.warning("Sentences too long for entailment")
                    self.weights[label] -= 1
                    continue
                
                if concat_before_return :
                    output = concat_batches(x1, len1, x2, len2, self.tokenizer.cls_token_id, self.tokenizer.sep_token_id, self.tokenizer.pad_token_id)
                    yield output, float(self.label_factory[label])
                else :
                    output1 = self.to_tensor(output1)
                    output2 = self.to_tensor(output2)
                    yield output1, output2, float(self.label_factory[label])

class NLIDataModule(LightningDataModule):
    def __init__(
        self,
        tokenizer: AutoTokenizer,
        config: Dict[str, Union[str, float, int]],
    ):
        super().__init__()

        if "train_data_path" not in config:
            logger.error(
                "train_data_path not found in the dataset configurations dictionary"
            )
            raise ValueError(
                "train_data_path not found in the dataset configurations dictionary"
            )

        if "val_data_path" not in config:
            logger.error(
                "val_data_path not found in the dataset configurations dictionary"
            )
            raise ValueError(
                "val_data_path not found in the dataset configurations dictionary"
            )

        if not config["eval_only"] :
            logger.info("Train dataset...")
            self.train_dataset = NLIDataset(tokenizer, config, train=True)
            logger.info("Valid dataset...")
            self.val_dataset = NLIDataset(tokenizer, config, train=False)
        if config.get("test_data_path", "") :
            logger.info("Test dataset...")
            config_copy = deepcopy(config)
            config_copy["val_data_path"] = config["test_data_path"]
            config_copy["val_n_samples"] = config["test_n_samples"]
            self.test_dataset = NLIDataset(tokenizer, config_copy, train=False)
        self.num_workers = config.get("num_workers", 2)
        self.batch_size = config["batch_size"]

    def train_dataloader(
        self,
    ) -> Union[DataLoader, List[DataLoader], Dict[str, DataLoader]]:
        return DataLoader(
            self.train_dataset,
            batch_size=self.batch_size,
            shuffle=True,
            num_workers=self.num_workers,
        )

    def val_dataloader(self) -> Union[DataLoader, List[DataLoader]]:
        return DataLoader(
            self.val_dataset, batch_size=self.batch_size, num_workers=self.num_workers
        )
        
    def test_dataloader(self) -> Union[DataLoader, List[DataLoader]]:
        return DataLoader(
            self.test_dataset, batch_size=self.batch_size, num_workers=self.num_workers
        )