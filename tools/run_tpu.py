# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.

"""Wrapper to train and test a video classification model."""
from tqdm import tqdm
from timesformer.utils.misc import launch_job
from timesformer.utils.parser import load_config, parse_args
# import torch.distributed as dist
from tools.test_net import test
# from tools.train_net import train_kinetic
import torch_xla as xla
import timesformer.utils.logging as logging
# logger = logging.get_logger(__name__)
# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.
import numpy as np
import pprint
import torch
from fvcore.nn.precise_bn import get_bn_modules, update_bn_stats
import torch.distributed as dist
import timesformer.models.losses as losses
import timesformer.models.optimizer as optim
import timesformer.utils.checkpoint as cu
import timesformer.utils.distributed as du
import timesformer.utils.logging as logging
import timesformer.utils.metrics as metrics
import timesformer.utils.misc as misc
import timesformer.visualization.tensorboard_vis as tb
from timesformer.datasets import loader
from timesformer.models import build_model
from timesformer.utils.meters import TrainMeter, ValMeter
from timesformer.utils.multigrid import MultigridSchedule
# from fvcore.common.registry import Registry
# import torch_xla.core.xla_model as xm
from torch.utils.data.distributed import DistributedSampler
from torch_xla import runtime as xr
import torch_xla.distributed.parallel_loader as pl
# MODEL_REGISTRY = Registry("MODEL")
from timm.data import Mixup
from timm.loss import LabelSmoothingCrossEntropy, SoftTargetCrossEntropy
import torch_xla as xla
import torch_xla.core.xla_model as xm
from torch_xla import runtime as xr
from timesformer.datasets.build import build_dataset
# device = xm.xla_device()
# logger = logging.get_logger(__name__)
import torch_xla.debug.metrics as met
import torch_xla.distributed.parallel_loader as pl
import torch_xla.debug.profiler as xp
import torch_xla.utils.utils as xu
import torch_xla.distributed.xla_multiprocessing as xmp

def train_epoch(
    train_loader, model, optimizer, train_meter, cur_epoch, cfg, writer=None, logger=None
):
    """
    Perform the video training for one epoch.
    Args:
        train_loader (loader): video training loader.
        model (model): the video model to train.
        optimizer (optim): the optimizer to perform optimization on the model's
            parameters.
        train_meter (TrainMeter): training meters to log the training performance.
        cur_epoch (int): current epoch of training.
        cfg (CfgNode): configs. Details can be found in
            slowfast/config/defaults.py
        writer (TensorboardWriter, optional): TensorboardWriter object
            to writer Tensorboard log.
    """

    logger.info('Turn of Enable .train() mode.')
    # model.train()
    train_meter.iter_tic()
    data_size = len(train_loader)
    # print('data_size', data_size)
    cur_global_batch_size = cfg.NUM_SHARDS * cfg.TRAIN.BATCH_SIZE
    num_iters = cfg.GLOBAL_BATCH_SIZE // cur_global_batch_size
    # assert False
    logger.info('Start looping for train loader...')
    for cur_iter, (inputs, labels, _, meta) in enumerate(train_loader):
        # assert False
        # with xp.StepTrace('train_kinetic', step_num=cur_iter):
            # logger.info('Transfer the data to the current GPU device.')
            # if cfg.TRAIN.TPU_ENABLE == False:
            #     if isinstance(inputs, (list,)):
            #         for i in range(len(inputs)):
            #             inputs[i] = inputs[i].to(device, non_blocking=True)
            #     else:
            #         inputs = inputs.to(device, non_blocking=True)
            #     labels = labels.to(device)
            #     for key, val in meta.items():
            #         if isinstance(val, (list,)):
            #             for i in range(len(val)):
            #                 val[i] = val[i].to(device,non_blocking=True)
            #         else:
            #             meta[key] = val.to(device,non_blocking=True)
            # elif cfg.TRAIN.TPU_ENABLE == True:
            #     pass
            # else:
            #     assert False, "Select incorrect NUM_GPUS"


        # logger.info('Update the learning rate.')
        lr = optim.get_epoch_lr(cur_epoch + float(cur_iter) / data_size, cfg)
        optim.set_lr(optimizer, lr)

        train_meter.data_toc()

        # logger.info('Explicitly declare reduction to mean.')
        if not cfg.MIXUP.ENABLED:
            loss_fun = losses.get_loss_func(cfg.MODEL.LOSS_FUNC)(reduction="mean")
        else:
            mixup_fn = Mixup(
                mixup_alpha=cfg.MIXUP.ALPHA, cutmix_alpha=cfg.MIXUP.CUTMIX_ALPHA, cutmix_minmax=cfg.MIXUP.CUTMIX_MINMAX, prob=cfg.MIXUP.PROB, switch_prob=cfg.MIXUP.SWITCH_PROB, mode=cfg.MIXUP.MODE,
                label_smoothing=0.1, num_classes=cfg.MODEL.NUM_CLASSES)
            hard_labels = labels
            inputs, labels = mixup_fn(inputs, labels)
            loss_fun = SoftTargetCrossEntropy()

        if cfg.DETECTION.ENABLE:
            preds = model(inputs, meta["boxes"])
        else:
            preds = model(inputs)

        # logger.info('Compute the loss.')
        loss = loss_fun(preds, labels)


        if cfg.MIXUP.ENABLED:
            labels = hard_labels

        # check Nan Loss.
        misc.check_nan_losses(loss)


        if cur_global_batch_size >= cfg.GLOBAL_BATCH_SIZE:
            optimizer.zero_grad()
            loss.backward()
            # Update the parameters.
            xm.optimizer_step(optimizer)
        else:
            if cur_iter == 0:
                optimizer.zero_grad()
            loss.backward()
            if (cur_iter + 1) % num_iters == 0:
                for p in model.parameters():
                    if(p.grad is not None):
                        p.grad /= num_iters
                    # optimizer.step()
                    xm.optimizer_step(optimizer)
                    optimizer.zero_grad()

        top1_err, top5_err = None, None
        if cfg.DATA.MULTI_LABEL:
            # Gather all the predictions across all the devices.
            if cfg.NUM_GPUS > 1:
                [loss] = du.all_reduce([loss])
            loss = loss.item()
        else:
            # Compute the errors.
            num_topks_correct = metrics.topks_correct(preds, labels, (1, 5))
            top1_err, top5_err = [
                (1.0 - x / preds.size(0)) * 100.0 for x in num_topks_correct
            ]
            # Gather all the predictions across all the devices.
            if cfg.NUM_GPUS > 1:
                loss, top1_err, top5_err = du.all_reduce(
                    [loss, top1_err, top5_err]
                )

            # Copy the stats from GPU to CPU (sync point).
            loss, top1_err, top5_err = (
                loss.item(),
                top1_err.item(),
                top5_err.item(),
            )

            # Update and log stats.
            train_meter.update_stats(
                top1_err,
                top5_err,
                loss,
                lr,
                inputs[0].size(0)
                * max(
                    cfg.NUM_GPUS, 1
                ),  # If running  on CPU (cfg.NUM_GPUS == 1), use 1 to represent 1 CPU.
            )

            if writer is not None:
                writer.add_scalars(
                    {
                        "Train/loss": loss,
                        "Train/lr": lr,
                        "Train/Top1_err": top1_err,
                        "Train/Top5_err": top5_err,
                    },
                    global_step=data_size * cur_epoch + cur_iter,
                )

        # xm.mark_step()
        train_meter.iter_toc()  # measure allreduce for this meter
        train_meter.log_iter_stats(cur_epoch, cur_iter)
        train_meter.iter_tic()

    # Log epoch stats.
    train_meter.log_epoch_stats(cur_epoch)
    train_meter.reset()

@torch.no_grad()
def eval_epoch(val_loader, model, val_meter, cur_epoch, cfg, writer=None):
    """
    Evaluate the model on the val set.
    Args:
        val_loader (loader): data loader to provide validation data.
        model (model): model to evaluate the performance.
        val_meter (ValMeter): meter instance to record and calculate the metrics.
        cur_epoch (int): number of the current epoch of training.
        cfg (CfgNode): configs. Details can be found in
            slowfast/config/defaults.py
        writer (TensorboardWriter, optional): TensorboardWriter object
            to writer Tensorboard log.
    """

    # Evaluation mode enabled. The running stats would not be updated.
    model.eval()
    val_meter.iter_tic()

    for cur_iter, (inputs, labels, _, meta) in enumerate(val_loader):
        preds = model(inputs)

        if cfg.DATA.MULTI_LABEL:
            if cfg.NUM_GPUS > 1:
                preds, labels = du.all_gather([preds, labels])
        else:
            # Compute the errors.
            num_topks_correct = metrics.topks_correct(preds, labels, (1, 5))

            # Combine the errors across the GPUs.
            top1_err, top5_err = [
                (1.0 - x / preds.size(0)) * 100.0 for x in num_topks_correct
            ]
            if cfg.NUM_GPUS > 1:
                top1_err, top5_err = du.all_reduce([top1_err, top5_err])

            # Copy the errors from GPU to CPU (sync point).
            top1_err, top5_err = top1_err.item(), top5_err.item()

            val_meter.iter_toc()
            # Update and log stats.
            val_meter.update_stats(
                top1_err,
                top5_err,
                inputs[0].size(0)
                * max(
                    cfg.NUM_GPUS, 1
                ),  # If running  on CPU (cfg.NUM_GPUS == 1), use 1 to represent 1 CPU.
            )
            # write to tensorboard format if available.
            if writer is not None:
                writer.add_scalars(
                    {"Val/Top1_err": top1_err, "Val/Top5_err": top5_err},
                    global_step=len(val_loader) * cur_epoch + cur_iter,
                )

        val_meter.update_predictions(preds, labels)

        val_meter.log_iter_stats(cur_epoch, cur_iter)
        val_meter.iter_tic()

    # Log epoch stats.
    val_meter.log_epoch_stats(cur_epoch)
    # write to tensorboard format if available.
    if writer is not None:
        if cfg.DETECTION.ENABLE:
            writer.add_scalars(
                {"Val/mAP": val_meter.full_map}, global_step=cur_epoch
            )
        else:
            all_preds = [pred.clone().detach() for pred in val_meter.all_preds]
            all_labels = [
                label.clone().detach() for label in val_meter.all_labels
            ]
            if cfg.NUM_GPUS:
                all_preds = [pred.cpu() for pred in all_preds]
                all_labels = [label.cpu() for label in all_labels]
            writer.plot_eval(
                preds=all_preds, labels=all_labels, global_step=cur_epoch
            )

    val_meter.reset()


def create_sampler(dataset, shuffle, cfg):
    """
    Create sampler for the given dataset.
    Args:
        dataset (torch.utils.data.Dataset): the given dataset.
        shuffle (bool): set to ``True`` to have the data reshuffled
            at every epoch.
        cfg (CfgNode): configs. Details can be found in
            slowfast/config/defaults.py
    Returns:
        sampler (Sampler): the created sampler.
    """
    if(cfg.TRAIN.TPU_ENABLE == False):
        sampler = DistributedSampler(dataset) if cfg.NUM_GPUS > 1 else None
    else:
        sampler = DistributedSampler(dataset, num_replicas=xr.world_size(), rank=xr.global_ordinal()) if xr.world_size() > 1 else None

    return sampler

def construct_loader(cfg, split, is_precise_bn=False):
    """
    Constructs the data loader for the given dataset.
    Args:
        cfg (CfgNode): configs. Details can be found in
            slowfast/config/defaults.py
        split (str): the split of the data loader. Options include `train`,
            `val`, and `test`.
    """
    assert split in ["train", "val", "test"]
    if split in ["train"]:
        dataset_name = cfg.TRAIN.DATASET
        batch_size = int(cfg.TRAIN.BATCH_SIZE / max(1, cfg.NUM_GPUS))
        shuffle = True
        drop_last = True
    elif split in ["val"]:
        dataset_name = cfg.TRAIN.DATASET
        batch_size = int(cfg.TRAIN.BATCH_SIZE / max(1, cfg.NUM_GPUS))
        shuffle = False
        drop_last = False
    elif split in ["test"]:
        dataset_name = cfg.TEST.DATASET
        batch_size = int(cfg.TEST.BATCH_SIZE / max(1, cfg.NUM_GPUS))
        shuffle = False
        drop_last = False

    # Construct the dataset
    dataset = build_dataset(dataset_name, cfg, split) 
    # print(dataset[1], dataset[1][0].shape, len(dataset[1]), len(dataset), xr.world_size())
    # print('Create a sampler for multi-process TRAIN.TPU_ENABLE')
    # print('Create a sampler for multi-process training')
    sampler = create_sampler(dataset, shuffle, cfg)
    # print('Create a loader')
    loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=(False if sampler else shuffle),
        sampler=sampler,
        num_workers=cfg.DATA_LOADER.NUM_WORKERS,
        # persistent_workers=True,
        # prefetch_factor=1,
    )
    return loader

def construct_fake_loader():
    train_dataset_len = 19881  # Roughly the size of Imagenet dataset.
    train_loader = xu.SampleGenerator(
        data=(torch.zeros(16, 3, 2, 224, 224),
              torch.zeros(16, dtype=torch.int64), 
              1,
              1),
        sample_count=train_dataset_len // 16 // xr.world_size())
    test_loader = xu.SampleGenerator(
        data=(torch.zeros(16, 3, 2, 224, 224),
              torch.zeros(16, dtype=torch.int64),
              1,
              1),
        sample_count=50000 // 16 // xr.world_size())
    return train_loader, test_loader

def calculate_and_update_precise_bn(loader, model, device, num_iters=200, use_gpu=True):
    """
    Update the stats in bn layers by calculate the precise stats.
    Args:
        loader (loader): data loader to provide training data.
        model (model): model to update the bn stats.
        num_iters (int): number of iterations to compute and update the bn stats.
        use_gpu (bool): whether to use GPU or not.
    """

    def _gen_loader():
        for inputs, *_ in loader:
            if use_gpu:
                if isinstance(inputs, (list,)):
                    for i in range(len(inputs)):
                        inputs[i] = inputs[i].to(device,non_blocking=True)
                else:
                    inputs = inputs.to(device,non_blocking=True)
            yield inputs

    # Update the bn stats.
    update_bn_stats(model, _gen_loader(), num_iters)

def train(cfg):
    """
    Train a video model for many epochs on train set and evaluate it on val set.
    Args:
        cfg (CfgNode): configs. Details can be found in
            slowfast/config/defaults.py
    """
    logger = logging.get_logger(__name__)

    # Set up environment.
    # logger.info("initialize distributed training...")
    dist.init_process_group(
            "xla", 
            init_method='xla://')

    train_loader_temp = construct_loader(cfg, "train")
    val_loader_temp = construct_loader(cfg, "val")


    torch.manual_seed(42)

    # Set random seed from configs.
    np.random.seed(cfg.RNG_SEED)
    torch.manual_seed(cfg.RNG_SEED)

    # logger.info("initialize xm.xla_device()...")
    device = xm.xla_device()

    # Setup logging format.
    logging.setup_logging(cfg.OUTPUT_DIR)

    # logger.info("Train with config:")
    # logger.info(pprint.pformat(cfg))

    # logger.info("Contruct model...")
    model = build_model(cfg).to(device)

    xm.broadcast_master_param(model)
    # Use multi-process data parallel model in the multi-gpu setting
    if cfg.NUM_GPUS > 1 :
        # Make model replica operate on the current device
        model = torch.nn.parallel.DistributedDataParallel(
            module=model, gradient_as_bucket_view=True, broadcast_buffers=False
        )

    if du.is_master_proc() and cfg.LOG_MODEL_INFO:
        misc.log_model_info(model, cfg, use_train_input=True)

    # Construct the optimizer.
    optimizer = optim.construct_optimizer(model, cfg)
    # logger.info("Contruct model...")
    # Load a checkpoint to resume training if applicable.
    if not cfg.TRAIN.FINETUNE:
      start_epoch = cu.load_train_checkpoint(cfg, model, optimizer)
    else:
      start_epoch = 0
      cu.load_checkpoint(cfg.TRAIN.CHECKPOINT_FILE_PATH, model)

    # logger.info("Contruct dataloader...")
    # Create the video train and val loaders.
    # train_loader, val_loader = construct_fake_loader()
    
    # print('Create MpDeviceLoader')
    train_loader = pl.MpDeviceLoader(train_loader_temp, device)
    val_loader = pl.MpDeviceLoader(val_loader_temp, device)

    logger.info("Contruct trainloader precise_bn_loader...")
    precise_bn_loader = (
        construct_loader(cfg, "train", is_precise_bn=True)
        if cfg.BN.USE_PRECISE_STATS
        else None
    )

    train_meter = TrainMeter(len(train_loader), cfg)
    val_meter = ValMeter(len(val_loader), cfg)
    # train_meter = None
    # val_meter = None
    logger.info("Set up writer...")
    # set up writer for logging to Tensorboard format.
    writer = tb.TensorboardWriter(cfg)


    # Perform the training loop.
    logger.info("Start epoch: {}".format(start_epoch + 1))
    print(f"Start epoch: {start_epoch + 1}")
    for cur_epoch in tqdm(range(start_epoch, cfg.SOLVER.MAX_EPOCH), unit="EPOCH"):

        # logger.info("Start train epoch")
        train_epoch(
            train_loader, model, optimizer, train_meter, cur_epoch, cfg, writer, logger
        )
        # logger.info("End train epoch")
        is_checkp_epoch = cu.is_checkpoint_epoch(
            cfg,
            cur_epoch,
            None,
        )
        is_eval_epoch = misc.is_eval_epoch(
            cfg, cur_epoch, None
        )

        # Compute precise BN stats.
        if (
            (is_checkp_epoch or is_eval_epoch)
            and cfg.BN.USE_PRECISE_STATS
            and len(get_bn_modules(model)) > 0
        ):
            calculate_and_update_precise_bn(
                precise_bn_loader,
                model,
                device,
                min(cfg.BN.NUM_BATCHES_PRECISE, len(precise_bn_loader)),
                cfg.NUM_GPUS > 0,
            )
        _ = misc.aggregate_sub_bn_stats(model)

        # Save a checkpoint.
        if is_checkp_epoch:
            cu.save_checkpoint(cfg.OUTPUT_DIR, model, optimizer, cur_epoch, cfg)
        # Evaluate the model on validation set.
        if is_eval_epoch:
            eval_epoch(val_loader, model, val_meter, cur_epoch, cfg, writer)

    if writer is not None:
        writer.close()

def get_func(cfg):
    train_func = train
    test_func = test
    return train_func, test_func

def _mp_fn(index, cfg):
    global CFG
    CFG = cfg
    torch.set_default_dtype(torch.float32)
    train(cfg)


if __name__ == "__main__":
    # main()
    args = parse_args()
    if args.num_shards > 1:
       args.output_dir = str(args.job_dir)

    # logger.info("LOAD CONFIG")
    cfg = load_config(args)

    # logger.info("LOAD TRAIN TEST FUNC")
    # train, test = get_func(cfg)

    # logger.info("START TRAINING")
    xla.launch(
                _mp_fn,\
                args=(cfg,),
                debug_single_process=0
            )
