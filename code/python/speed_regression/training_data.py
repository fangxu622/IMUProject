import argparse
import time
from numba import jit
import numpy as np
import quaternion
import pandas
from scipy.fftpack import fft
from scipy.ndimage.filters import gaussian_filter1d
import matplotlib.pyplot as plt


class TrainingDataOption:
    def __init__(self, sample_step=10, window_size=200, feature='fourier', frq_threshold=10, discard_direct=False,
                 feature_smooth_sigma=None, speed_smooth_sigma=50.0):
        self.sample_step_ = sample_step
        self.window_size_ = window_size
        self.frq_threshold_ = frq_threshold
        self.feature_ = feature
        self.nanoToSec = 1000000000.0
        self.discard_direct_ = discard_direct
        self.feature_smooth_sigma_ = feature_smooth_sigma
        self.speed_smooth_sigma_ = speed_smooth_sigma


#@jit
def compute_fourier_features(data, samples, window_size, threshold, discard_direct=False):
    """
    Compute fourier coefficients as feature vector
    :param data: NxM array for N samples with M dimensions
    :return: Nxk array
    """
    skip = 0
    if discard_direct:
        skip = 1
    features = np.empty([samples.shape[0], data.shape[1] * (threshold - skip)], dtype=np.float)
    for i in range(samples.shape[0]):
        features[i, :] = np.abs(fft(data[samples[i]-window_size:samples[i]], axis=0)[skip:threshold]).flatten()
    return features


#@jit
def compute_direct_features(data, samples, window_size):
    features = np.empty([samples.shape[0], data.shape[1] * window_size], dtype=np.float)
    for i in range(samples.shape[0]):
        features[i, :] = data[samples[i] - window_size:samples[i]].flatten()
    return features
    # return [list(data[ind - option.window_size_:ind].values.flatten())
    #         for ind in samples]


def get_training_data(data_all, imu_columns, option, sample_points=None):
    """
    Create training data.
    :param data_all: The whole dataset. Must include 'time' column and all columns inside imu_columns
    :param imu_columns: Columns used for constructing feature vectors. Fields must exist in the dataset
    :return: [Nx(d+1)] array. Target value is appended at back
    """
    N = data_all.shape[0]
    if sample_points is None:
        sample_points = np.arange(option.window_size_,
                                  N - 1,
                                  option.sample_step_,
                                  dtype=int)
    assert sample_points[-1] < N

    pose_data = data_all[['pos_x', 'pos_y', 'pos_z']].values
    data_used = data_all[imu_columns].values
    time_stamp = data_all['time'].values / 1e09

    speed_all = np.zeros(N)
    speed_all[1:-1] = np.divide(np.linalg.norm(pose_data[2:] - pose_data[:-2], axis=1),
                                time_stamp[2:] - time_stamp[:-2])
    # filter the speed with gaussian filter
    if option.speed_smooth_sigma_ > 0:
        speed_all = gaussian_filter1d(speed_all, option.speed_smooth_sigma_, axis=0)

    if option.feature_ == 'direct':
        #local_imu_list = compute_direct_features(data_used, sample_points, option.window_size_)
        local_imu_list = [data_used[ind - option.window_size_:ind].flatten() for ind in sample_points]
    elif option.feature_ == 'fourier':
        local_imu_list = compute_fourier_features(data_used, sample_points, option.window_size_, option.frq_threshold_,
                                                  option.discard_direct_)
        # local_imu_list = [compute_fourier_feature(data_used[ind-option.window_size_:ind].values, option.frq_threshold_)
        #                   .flatten('F') for ind in sample_points]
    else:
        print('Feature type not supported: ' + option.feature_)
        raise ValueError

    return np.concatenate([local_imu_list, speed_all[sample_points, None]], axis=1)


def get_orientation_training_data(data_all, camera_orientation, imu_columns, options, sample_points=None):
    """
    Get the 2D view angle feature.
    :param data_all:
    :param imu_columns:
    :param options:
    :param sample_points:
    :return:
    """
    assert camera_orientation.shape[0] == data_all.shape[0]
    if sample_points is None:
        sample_points = np.arange(option.window_size_,
                                  N - 1,
                                  option.sample_step_,
                                  dtype=int)
    # Avoid out of bound error
    if sample_points[-1] == data_all.shape[0] - 1:
        sample_points[-1] -= 1
    if sample_points[0] == 0:
        sample_points[0] = 1
    # Use the same algorithm to compute feature vectors
    feature_mat = get_training_data(data_all, imu_columns, option=options, sample_points=sample_points)
    # Compute the cosine of the angle between camera viewing angle and moving angle
    position_data = data_all[['pos_x', 'pos_y']].values
    moving_dir = (position_data[sample_points + 1] - position_data[sample_points - 1])
    camera_axis_local = np.array([0., 0., -1.])
    for i in range(sample_points.shape[0]):
        q = quaternion.quaternion(*camera_orientation[sample_points[i]])
        camera_axis = (q * quaternion.quaternion(1., *camera_axis_local) * q.conj()).vec[:2]
        feature_mat[i, -1] = min(np.dot(moving_dir[i], camera_axis) / np.linalg.norm(moving_dir[i]), 1.0)
    return feature_mat


def split_data(data, ratio=0.3):
    """
    Randomly split data set
    :param data: all data
    :param ratio: ratio of hold-off set
    :return: set1, set2
    """
    mask = np.random.random(data.shape[0]) < ratio
    return data[mask], data[~mask]

# for tests
if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('dir')
    parser.add_argument('--window', default=300, type=int)
    parser.add_argument('--step', default=50, type=int)
    parser.add_argument('--feature', default='direct', type=str)
    parser.add_argument('--frq_threshold', default=50, type=int)
    parser.add_argument('--speed_smooth_sigma', default=0.0, type=float)

    args = parser.parse_args()

    data_dir = args.dir + '/processed'
    print('Loading dataset ' + data_dir + '/data.csv')
    data_all = pandas.read_csv(data_dir + '/data.csv')
    option = TrainingDataOption(window_size=args.window, sample_step=args.step, frq_threshold=args.frq_threshold,
                                feature=args.feature, speed_smooth_sigma=args.speed_smooth_sigma)
    # Create a small sample for testing
    N = data_all.shape[0]
    imu_columns = ['gyro_x', 'gyro_y', 'gyro_z', 'acce_x', 'acce_y', 'acce_z']

    nano_to_sec = 1e09
    time_stamp = data_all['time'].values / nano_to_sec
    orientation = data_all[['ori_w', 'ori_x', 'ori_y', 'ori_z']].values
    positions_xy = data_all[['pos_x', 'pos_y']].values

    training_set = get_orientation_training_data(data_all, orientation, imu_columns, option)

    sample_points = np.arange(option.window_size_,
                              N - 1,
                              option.sample_step_,
                              dtype=int)
    speed_mag = np.linalg.norm((positions_xy[sample_points+1] - positions_xy[sample_points-1]) /
                (time_stamp[sample_points+1] - time_stamp[sample_points-1])[:, None], axis=1)
    cos_array = training_set[:, -1]
    speed_forward = speed_mag * cos_array
    speed_tangent = speed_mag * np.sqrt(1.0 - cos_array ** 2)

    plt.figure('Orthogonal speed')
    plt.plot(time_stamp[sample_points], speed_mag)
    plt.plot(time_stamp[sample_points], speed_forward)
    plt.plot(time_stamp[sample_points], speed_tangent)
    plt.legend(['Mag', 'Forward', 'Tangent'])
    plt.show()