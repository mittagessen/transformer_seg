#
# Copyright 2022 Benjamin Kiessling
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
# or implied. See the License for the specific language governing
# permissions and limitations under the License.
"""
party.cli.train
~~~~~~~~~~~~~~~~~~

Command line driver for recognition training.
"""
import logging

import click
from threadpoolctl import threadpool_limits

from transformer_seg.default_specs import SEGMENTATION_HYPER_PARAMS

from .util import _expand_gt, _validate_manifests, message, to_ptl_device

logging.captureWarnings(True)
logger = logging.getLogger('transformer_seg')

# suppress worker seeding message
logging.getLogger("lightning.fabric.utilities.seed").setLevel(logging.ERROR)


@click.command('train')
@click.pass_context
@click.option('-i', '--load', default=None, type=click.Path(exists=True), help='Checkpoint to load')
@click.option('-B', '--batch-size', show_default=True, type=click.INT,
              default=SEGMENTATION_HYPER_PARAMS['batch_size'], help='batch sample size')
@click.option('-o', '--output', show_default=True, type=click.Path(), default='model', help='Output model file')
@click.option('-F', '--freq', show_default=True, default=SEGMENTATION_HYPER_PARAMS['freq'], type=click.FLOAT,
              help='Model saving and report generation frequency in epochs '
                   'during training. If frequency is >1 it must be an integer, '
                   'i.e. running validation every n-th epoch.')
@click.option('-q',
              '--quit',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['quit'],
              type=click.Choice(['early',
                                 'fixed']),
              help='Stop condition for training. Set to `early` for early stooping or `fixed` for fixed number of epochs')
@click.option('-N',
              '--epochs',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['epochs'],
              help='Number of epochs to train for')
@click.option('--min-epochs',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['min_epochs'],
              help='Minimal number of epochs to train for when using early stopping.')
@click.option('--lag',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['lag'],
              help='Number of evaluations (--report frequency) to wait before stopping training without improvement')
@click.option('--min-delta',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['min_delta'],
              type=click.FLOAT,
              help='Minimum improvement between epochs to reset early stopping. Default is scales the delta by the best loss')
@click.option('--optimizer',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['optimizer'],
              type=click.Choice(['Adam',
                                 'AdamW',
                                 'SGD',
                                 'RMSprop']),
              help='Select optimizer')
@click.option('-r', '--lrate', show_default=True, default=SEGMENTATION_HYPER_PARAMS['lr'], help='Learning rate')
@click.option('-m', '--momentum', show_default=True, default=SEGMENTATION_HYPER_PARAMS['momentum'], help='Momentum')
@click.option('-w', '--weight-decay', show_default=True, type=float,
              default=SEGMENTATION_HYPER_PARAMS['weight_decay'], help='Weight decay')
@click.option('--gradient-clip-val', show_default=True, default=SEGMENTATION_HYPER_PARAMS['gradient_clip_val'], help='Gradient clip value')
@click.option('--warmup', show_default=True, type=int,
              default=SEGMENTATION_HYPER_PARAMS['warmup'], help='Number of steps to ramp up to `lrate` initial learning rate.')
@click.option('--schedule',
              show_default=True,
              type=click.Choice(['constant',
                                 '1cycle',
                                 'exponential',
                                 'cosine',
                                 'step',
                                 'reduceonplateau']),
              default=SEGMENTATION_HYPER_PARAMS['schedule'],
              help='Set learning rate scheduler. For 1cycle, cycle length is determined by the `--epoch` option.')
@click.option('-g',
              '--gamma',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['gamma'],
              help='Decay factor for exponential, step, and reduceonplateau learning rate schedules')
@click.option('-ss',
              '--step-size',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['step_size'],
              help='Number of validation runs between learning rate decay for exponential and step LR schedules')
@click.option('--sched-patience',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['rop_patience'],
              help='Minimal number of validation runs between LR reduction for reduceonplateau LR schedule.')
@click.option('--cos-max',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['cos_t_max'],
              help='Epoch of minimal learning rate for cosine LR scheduler.')
@click.option('--cos-min-lr',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['cos_min_lr'],
              help='Minimal final learning rate for cosine LR scheduler.')
@click.option('-t', '--training-files', show_default=True, default=None, multiple=True,
              callback=_validate_manifests, type=click.File(mode='r', lazy=True),
              help='File(s) with additional paths to training data')
@click.option('-e', '--evaluation-files', show_default=True, default=None, multiple=True,
              callback=_validate_manifests, type=click.File(mode='r', lazy=True),
              help='File(s) with paths to evaluation data. Overrides the `-p` parameter')
@click.option('--workers', show_default=True, default=1, type=click.IntRange(1), help='Number of worker processes.')
@click.option('--threads', show_default=True, default=1, type=click.IntRange(1), help='Maximum size of OpenMP/BLAS thread pool.')
@click.option('--augment/--no-augment',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['augment'],
              help='Enable image augmentation')
@click.option('--accumulate-grad-batches',
              show_default=True,
              default=SEGMENTATION_HYPER_PARAMS['accumulate_grad_batches'],
              help='Number of batches to accumulate gradient across.')
@click.argument('ground_truth', nargs=-1, callback=_expand_gt, type=click.Path(exists=False, dir_okay=False))
def train(ctx, load, batch_size, output, freq, quit, epochs,
          min_epochs, lag, min_delta, optimizer, lrate, momentum, weight_decay,
          gradient_clip_val, warmup, schedule, gamma, step_size,
          sched_patience, cos_max, cos_min_lr, training_files,
          evaluation_files, workers, threads, augment, accumulate_grad_batches,
          ground_truth):
    """
    Trains a model from image-text pairs.
    """
    if not (0 <= freq <= 1) and freq % 1.0 != 0:
        raise click.BadOptionUsage('freq', 'freq needs to be either in the interval [0,1.0] or a positive integer.')

    if augment:
        try:
            import albumentations  # NOQA
        except ImportError:
            raise click.BadOptionUsage('augment', 'augmentation needs the `albumentations` package installed.')

    import torch

    from transformer_seg.dataset import LineSegmentationDataModule
    from transformer_seg.model import SegmentationModel

    from lightning.pytorch import Trainer
    from lightning.pytorch.callbacks import RichModelSummary, ModelCheckpoint, RichProgressBar

    torch.set_float32_matmul_precision('medium')

    hyper_params = SEGMENTATION_HYPER_PARAMS.copy()
    hyper_params.update({'freq': freq,
                         'batch_size': batch_size,
                         'quit': quit,
                         'epochs': epochs,
                         'min_epochs': min_epochs,
                         'lag': lag,
                         'min_delta': min_delta,
                         'optimizer': optimizer,
                         'lr': lrate,
                         'momentum': momentum,
                         'weight_decay': weight_decay,
                         'warmup': warmup,
                         'schedule': schedule,
                         'gamma': gamma,
                         'step_size': step_size,
                         'rop_patience': sched_patience,
                         'cos_t_max': cos_max,
                         'cos_min_lr': cos_min_lr,
                         'augment': augment,
                         'accumulate_grad_batches': accumulate_grad_batches,
                         'gradient_clip_val': gradient_clip_val,
                         })

    ground_truth = list(ground_truth)

    # merge training_files into ground_truth list
    if training_files:
        ground_truth.extend(training_files)

    if len(ground_truth) == 0:
        raise click.UsageError('No training data was provided to the train command. Use `-t` or the `ground_truth` argument.')

    try:
        accelerator, device = to_ptl_device(ctx.meta['device'])
    except Exception as e:
        raise click.BadOptionUsage('device', str(e))

    if hyper_params['freq'] > 1:
        val_check_interval = {'check_val_every_n_epoch': int(hyper_params['freq'])}
    else:
        val_check_interval = {'val_check_interval': hyper_params['freq']}

    data_module = LineSegmentationDataModule(training_data=ground_truth,
                                             evaluation_data=evaluation_files,
                                             augmentation=augment,
                                             batch_size=batch_size,
                                             num_workers=workers)

    cbs = [RichModelSummary(max_depth=2)]

    checkpoint_callback = ModelCheckpoint(dirpath=output,
                                          save_top_k=10,
                                          monitor='global_step',
                                          mode='max',
                                          auto_insert_metric_name=False,
                                          filename='checkpoint_{epoch:02d}-{val_metric:.4f}')

    cbs.append(checkpoint_callback)
    if not ctx.meta['verbose']:
        cbs.append(RichProgressBar(leave=True))

    trainer = Trainer(accelerator=accelerator,
                      devices=device,
                      precision=ctx.meta['precision'],
                      max_epochs=hyper_params['epochs'] if hyper_params['quit'] == 'fixed' else -1,
                      min_epochs=hyper_params['min_epochs'],
                      enable_progress_bar=True if not ctx.meta['verbose'] else False,
                      deterministic=ctx.meta['deterministic'],
                      enable_model_summary=False,
                      accumulate_grad_batches=hyper_params['accumulate_grad_batches'],
                      callbacks=cbs,
                      gradient_clip_val=hyper_params['gradient_clip_val'],
                      **val_check_interval)

    with trainer.init_module():
        if load:
            message('Loading model.')
            model = SegmentationModel.load_from_checkpoint(load, **hyper_params)

        else:
            message('Initializing model.')
            model = SegmentationModel(**hyper_params)

    with threadpool_limits(limits=threads):
        trainer.fit(model, data_module)

    if model.best_epoch == -1:
        logger.warning('Model did not improve during training.')
        ctx.exit(1)

    if not model.current_epoch:
        logger.warning('Training aborted before end of first epoch.')
        ctx.exit(1)

    print(f'Best model {checkpoint_callback.best_model_path}')
