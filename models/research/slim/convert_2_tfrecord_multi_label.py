# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Converts HICO data to TFRecords file format with Example protos.

The raw HICO data set is expected to reside in JPEG files located in the
following directory structure.

  hico_data/images/train2015/HICO_train2015_00000001.jpg
  hico_data/images/test2015/HICO_test2015_00000001.jpg
  ...

The training data set consists of 38,116 JPEG images.

The test data set consists of 9,658 JPEG images.

This TensorFlow script converts the training and test data into
a sharded data set consisting of 32 and 16 TFRecord files, respectively.

  train_directory/train-00000-of-00032
  train_directory/train-00001-of-00032
  ...
  train_directory/train-00031-of-00032

and

  test_directory/validation-00000-of-00016
  test_directory/validation-00001-of-00016
  ...
  test_directory/validation-00015-of-00016

Each test TFRecord file contains ~600 records. Each training TFREcord
file contains ~1190 records. Each record within the TFRecord file is a
serialized Example proto. The Example proto contains the following fields:

  image/encoded: string containing JPEG encoded image in RGB colorspace
  image/height: integer, image height in pixels
  image/width: integer, image width in pixels
  image/colorspace: string, specifying the colorspace, always 'RGB'
  image/channels: integer, specifying the number of channels, always 3
  image/format: string, specifying the format, always'JPEG'

  image/filename: string containing the basename of the image file
            e.g. 'HICO_train2015_00000006.jpg'
 
  image/class/label: list of integers specifying the classification indices.
  image/class/object: list of objects  
  image/class/verb: list of verbs

"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from datetime import datetime
import os
import random
import sys
import threading

#import google3
import numpy as np
import tensorflow as tf

output_dir='/home/atsg/PycharmProjects/gvh205_py3/tensorflow_slim/outputs/test'
num_class=31

data_dir = '/home/atsg/PycharmProjects/gvh205_py3/tensorflow_slim/pytorch/data/PETA_tfrecord'
label_map_path = os.path.join(data_dir, 'label_map.txt')
label_train_path = os.path.join(data_dir, 'label_train.txt')
label_test_path = os.path.join(data_dir, 'label_test.txt')
image_train_path = os.path.join(data_dir, 'image_train.txt')
image_test_path = os.path.join(data_dir, 'image_test.txt')


tf.app.flags.DEFINE_string('data_dir', data_dir,
						   'Data directory')
tf.app.flags.DEFINE_string('output_dir', output_dir,
                           'Output data directory')

tf.app.flags.DEFINE_integer('train_shards', 32,
                            'Number of shards in training TFRecord files.')
tf.app.flags.DEFINE_integer('test_shards', 4,
                            'Number of shards in validation TFRecord files.')

tf.app.flags.DEFINE_integer('num_threads', 4,
                            'Number of threads to preprocess the images.')


# This file contains mapping from label to adj description.
# Assumes each line of the file looks like:
#
#   0 airplane board
#   28 bird release
#   39 boat row
#
# where each line corresponds to a unique mapping. Note that each line is
# formatted as <label> <object> <verb>.
tf.app.flags.DEFINE_string('label_text', label_map_path,
                           'Label description file')

# This file contains training labels
# Assumes each line of the file looks like:
#
#   576 577 578
#
tf.app.flags.DEFINE_string('labels_train', label_train_path,
                           'Training label file')
tf.app.flags.DEFINE_string('labels_test', label_test_path,
						   'Test label file')
tf.app.flags.DEFINE_integer('num_classes',num_class,
                           'Number of classes')

# This file contains training filenames
# Assumes each line of the file looks like:
#
#	HICO_train2015_00000036.jpg
#
tf.app.flags.DEFINE_string('image_train',image_train_path,
						   'Training filenames file')
tf.app.flags.DEFINE_string('image_test',image_test_path,
						   'Testing filenames file')

FLAGS = tf.app.flags.FLAGS


def _int64_feature(value):
  """Wrapper for inserting int64 features into Example proto."""
  if not isinstance(value, list):
    value = [value]
  return tf.train.Feature(int64_list=tf.train.Int64List(value=value))


def _float_feature(value):
  """Wrapper for inserting float features into Example proto."""
  if not isinstance(value, list):
    value = [value]
  return tf.train.Feature(float_list=tf.train.FloatList(value=value))


def _bytes_feature(value):
  """Wrapper for inserting bytes features into Example proto."""
  if not isinstance(value, list):
    value = [value]
  return tf.train.Feature(bytes_list=tf.train.BytesList(value=value))


def _convert_to_example(image_data, filename, label, label_text, height, width):
  """Build an Example proto for an example.

  Args:
    image_data: string, JPEG encoding of RGB image
    filename: string, filename of an image
    label: list of floats, identifier for the ground truth 
    label_text: list of strings, e.g. ['airplane board', 'airplane ride'] 
    height: integer, image height in pixels
    width: integer, image width in pixels
  Returns:
    Example proto
  """

  colorspace = 'RGB'
  channels = 3
  image_format = 'JPEG'

  adj = []
  
  for text in label_text:
   assert len(text) == 1
   adj.append(text[0])

  
  example = tf.train.Example(features=tf.train.Features(feature={
      'image/height': _int64_feature(height),
      'image/width': _int64_feature(width),
      'image/colorspace': _bytes_feature(colorspace.encode()),
      'image/channels': _int64_feature(channels), 
      'image/format': _bytes_feature(image_format.encode()),
      'image/filename': _bytes_feature(filename.encode()),
      'image/encoded': _bytes_feature(image_data),
      'image/class/label': _bytes_feature(label),   # of shape (600,)
      'image/class/adj': _bytes_feature([x.encode() for x in adj])}))
  return example


class ImageCoder(object):
  """Helper class that provides TensorFlow image coding utilities."""

  def __init__(self):
    # Create a single Session to run all image coding calls.
    self._sess = tf.Session()

    # Initializes function that converts PNG to JPEG data.
    self._png_data = tf.placeholder(dtype=tf.string)
    image = tf.image.decode_png(self._png_data, channels=3)
    self._png_to_jpeg = tf.image.encode_jpeg(image, format='rgb', quality=100)

    # Initializes function that converts CMYK JPEG data to RGB JPEG data.
    self._cmyk_data = tf.placeholder(dtype=tf.string)
    image = tf.image.decode_jpeg(self._cmyk_data, channels=0)
    self._cmyk_to_rgb = tf.image.encode_jpeg(image, format='rgb', quality=100)

    # Initializes function that decodes RGB JPEG data.
    self._decode_jpeg_data = tf.placeholder(dtype=tf.string)
    self._decode_jpeg = tf.image.decode_jpeg(self._decode_jpeg_data, channels=3)

  def png_to_jpeg(self, image_data):
    return self._sess.run(self._png_to_jpeg,
                          feed_dict={self._png_data: image_data})

  def cmyk_to_rgb(self, image_data):
    return self._sess.run(self._cmyk_to_rgb,
                          feed_dict={self._cmyk_data: image_data})

  def decode_jpeg(self, image_data):
    image = self._sess.run(self._decode_jpeg,
                           feed_dict={self._decode_jpeg_data: image_data})
    assert len(image.shape) == 3
    assert image.shape[2] == 3
    return image


def _process_image(data_dir, filename, coder):
  """Process a single image file.

  Args:
    data_dir: string, root directory of images
    filename: string, filename of an image file.
    coder: instance of ImageCoder to provide TensorFlow image coding utils.
  Returns:
    image_data: string, JPEG encoding of RGB image.
    height: integer, image height in pixels.
    width: integer, image width in pixels.
  """
  # Read the image file.
  file_path = os.path.join(data_dir, filename)
  image_data = tf.gfile.FastGFile(file_path, 'rb').read()

  # Decode the RGB JPEG.
  image = coder.decode_jpeg(image_data)

  # Check that image converted to RGB
  assert len(image.shape) == 3
  height = image.shape[0]
  width = image.shape[1]
  assert image.shape[2] == 3

  return image_data, height, width


def _get_output_filename(output_dir, name, shard, num_shards):
    output_filename = '%s-%.5d-of-%.5d.tfrecord' % (name, shard, num_shards)
    output_file = os.path.join(output_dir, output_filename)
    return output_file


def _process_dataset(name, data_dir, filenames_file, labels_file, 
                    num_shards, label_to_text, output_dir):
  """Process a complete data set and save it as a TFRecord.

  Args:
    name: string, unique identifier specifying the data set.
    data_dir: string, root path to the data set.
    filenames_file: string, path to the filenames file
    labels_file: string, path to the label file
    num_shards: integer number of shards for this data set.
    label_to_text: dict of label to adj descriptions, e.g.,
      0 --> 'airplane, board'
    output_dir: string, path to the output directory
  """
      
  lines = tf.gfile.FastGFile(filenames_file, 'r').readlines()
  filenames = [l.strip() for l in lines]
  
  lines = tf.gfile.FastGFile(labels_file, 'r').readlines()
  labels = [] 
  labels_text = []
  count=0
  for l in lines:
    print (count)
    count+=1
    parts = l.strip().split(' ')
    #Encode label to num_class-dim vectors
    encoded_label = np.zeros(FLAGS.num_classes, dtype=np.float32) 
    text_list = []
    for part in parts:
      encoded_label[int(part)] = 1.0
      text_list.append(label_to_text[int(part)])
    labels.append(encoded_label.tostring())
    labels_text.append(text_list)

  """
  # Shuffle the ordering of all image files in order to guarantee
  # random ordering of the images with respect to label in the
  # saved TFRecord files. Make the randomization repeatable.
  shuffled_index = range(len(filenames))
  random.seed(12345)
  random.shuffle(shuffled_index)

  filenames = [filenames[i] for i in shuffled_index]
  labels = [labels[i] for i in shuffled_index]
  labels_text = [labels_text[i] for i in shuffled_index]
  """

  # Break all images <num_shards> shards
  spacing = np.linspace(0, len(filenames), num_shards + 1).astype(np.int)
  ranges = []
  for i in range(len(spacing) - 1):
    ranges.append([spacing[i], spacing[i+1]])

  # Create a generic TensorFlow-based utility for converting all image codings
  coder = ImageCoder()
  
  counter = 0
  for i in range(len(ranges)):
    # Open new TFRecord file
    tf_filename = _get_output_filename(output_dir, name, i, num_shards)
    files_in_shard = np.arange(ranges[i][0], ranges[i][1], dtype=int)
    shard_counter = 0
    
    with tf.python_io.TFRecordWriter(tf_filename) as writer:
      for j in files_in_shard:
        filename = filenames[j]
        label = labels[j]
        text = labels_text[j]
        
        image_data, height, width = _process_image(data_dir, filename, coder)
      
        example = _convert_to_example(image_data, filename, label, text, height, width)
        writer.write(example.SerializeToString())

        shard_counter += 1
        counter += 1

        print('[%s] Processed image %d/%d' % (datetime.now(), counter, len(filenames)))
        sys.stdout.flush()
    
    print('[%s] Wrote %d images to %s' %(datetime.now(), shard_counter, tf_filename))
    sys.stdout.flush()

  print('[%s] Finished converting %d images to %d shards' %(datetime.now(), counter, len(ranges)))
  sys.stdout.flush()

def _build_label_lookup(label_text):
  """Build lookup for label to adj description.

  Args:
    label_text: string, path to file containing mapping from
      label to adj description.

      Assumes each line of the file looks like:

        0    airplane board
        1	 airplane direct
        2	 airplane exit

      where each line corresponds to a unique mapping. Note that each line is
      formatted as <label> <object> <verb>.

  Returns:
    Dictionary of synset to human labels, such as:
      0 --> 'airplane board'
  """
  lines = tf.gfile.FastGFile(label_text, 'r').readlines()
  label_to_text = {}
  for l in lines:
    if l:
      parts = l.strip().split(' ')
      assert len(parts) == 2
      label = int(parts[0])
      text = parts[1:]
      label_to_text[label] = text
  return label_to_text




def main(unused_argv):
  assert not FLAGS.train_shards % FLAGS.num_threads, (
      'Please make the FLAGS.num_threads commensurate with FLAGS.train_shards')
  assert not FLAGS.test_shards % FLAGS.num_threads, (
      'Please make the FLAGS.num_threads commensurate with '
      'FLAGS.validation_shards')
  
  if not os.path.exists(FLAGS.output_dir):
      os.mkdir(FLAGS.output_dir)
  
  print('Saving results to %s' % FLAGS.output_dir)

  # Build a map from label to adj descriptions.
  label_text_file = os.path.join(FLAGS.data_dir, FLAGS.label_text)
  label_to_text = _build_label_lookup(label_text_file)
  #print(label_to_text[0])
  #print(label_to_text[578])
  
  image_train_file = os.path.join(FLAGS.data_dir, FLAGS.image_train)
  image_test_file = os.path.join(FLAGS.data_dir, FLAGS.image_test)
  labels_train_file = os.path.join(FLAGS.data_dir, FLAGS.labels_train)
  labels_test_file = os.path.join(FLAGS.data_dir, FLAGS.labels_test)
  test_dir = os.path.join(FLAGS.data_dir, 'images', 'test')
  train_dir = os.path.join(FLAGS.data_dir, 'images', 'train')
  test_output_dir = os.path.join(FLAGS.output_dir, 'test')
  train_output_dir = os.path.join(FLAGS.output_dir, 'train')

  if not os.path.exists(test_output_dir):
      os.makedirs(test_output_dir)
  if not os.path.exists(train_output_dir):
      os.makedirs(train_output_dir)

  # Run it!
  _process_dataset('test', test_dir, image_test_file,
                   labels_test_file, FLAGS.test_shards, label_to_text,
                   test_output_dir)
  
  _process_dataset('train', train_dir, image_train_file,
                   labels_train_file, FLAGS.train_shards,  label_to_text,
                   train_output_dir)

  
if __name__ == '__main__':
  tf.app.run()
