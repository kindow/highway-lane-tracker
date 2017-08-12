#!/usr/bin/env python
"""
Finds lane lines and their curvature from dashcam video.

Author: Peter Moran
Created: 8/1/2017
"""
import glob

import cv2
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage.filters import convolve as center_convolve

from udacity_tools import overlay_centroids


class DynamicSubplot:
    def __init__(self, m, n):
        self.figure, self.plots = plt.subplots(m, n)
        self.plots = self.plots.flatten()
        self.curr_plot = 0

    def imshow(self, img, title, cmap=None):
        self.plots[self.curr_plot].imshow(img, cmap=cmap)
        self.plots[self.curr_plot].set_title(title)
        self.curr_plot += 1

    def skip_plot(self):
        self.plots[self.curr_plot].axis('off')
        self.curr_plot += 1


def find_object_img_points(image_fnames, chess_rows, chess_cols):
    # Create object and image point pairings
    chess_corners = np.zeros((chess_cols * chess_rows, 3), np.float32)
    chess_corners[:, :2] = np.mgrid[0:chess_rows, 0:chess_cols].T.reshape(-1, 2)
    objpoints = []  # 3d points in real world space
    imgpoints = []  # 2d points in image plane.
    for fname in image_fnames:
        # Load images
        img = cv2.imread(fname)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Find the chessboard corners
        found, img_corners = cv2.findChessboardCorners(gray, (chess_rows, chess_cols), None)

        # If found, save object points, image points
        if found:
            objpoints.append(chess_corners)
            imgpoints.append(img_corners)

    return objpoints, imgpoints


def calibrate(objpoints, imgpoints, img_size):
    """
    :return: The computed (camera matrix, distortion coefficients).
    """
    sucess, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(objpoints, imgpoints, img_size, None, None)
    if not sucess:
        return None
    return camera_matrix, dist_coeffs


def threshold_lanes(image, base_threshold=50, thresh_window=411):
    # Mask the image
    binary = cv2.adaptiveThreshold(
        image,
        maxValue=255, adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        thresholdType=cv2.THRESH_BINARY,
        blockSize=thresh_window,
        C=base_threshold * -1)

    return binary


def get_overhead_transform(dx, dy):
    assert (dy, dx) == (720, 1280), "Unexpected image size."
    # Define points
    top_left = (584, 458)
    top_right = (701, 458)
    bottom_left = (295, 665)
    bottom_right = (1022, 665)
    source = np.float32([top_left, top_right, bottom_right, bottom_left])
    destination = np.float32([(bottom_left[0], 0), (bottom_right[0], 0),
                              (bottom_right[0], dy), (bottom_left[0], dy)])
    M_trans = cv2.getPerspectiveTransform(source, destination)
    return M_trans


def transform_to_overhead(image):
    # Transform to overhead image
    dy, dx = image.shape[0:2]
    M_trans = get_overhead_transform(dx, dy)
    overhead_img = cv2.warpPerspective(image, M_trans, (dx, dy))

    return overhead_img


def find_window_centroids(image, window_width, window_height, margin):
    img_h, img_w = image.shape[0:2]
    window_lr_centroids = []  # Store the (left,right) window centroid positions per level

    # Find the start of the line, along the bottom of the image
    search_strip = image[2 * img_h // 3:, :]  # search bottom 1/3rd of image
    strip_scores = score_columns(search_strip, window_width)
    l_line_center = argmax_between(strip_scores, begin=0, end=img_w // 2)
    r_line_center = argmax_between(strip_scores, begin=img_w // 2, end=img_w)

    # Add what we found for the first layer
    window_lr_centroids.append((l_line_center, r_line_center))

    # Go through each layer looking for max pixel locations
    for level in range(1, (int)(image.shape[0] / window_height)):
        search_strip = image[int(img_h - (level + 1) * window_height):int(img_h - level * window_height), :]
        strip_scores = score_columns(search_strip, window_width)

        # Find the best left centroid nearby the centroid from the row below
        l_search_min = int(max(l_line_center - margin, 0))
        l_search_max = int(min(l_line_center + margin, img_w))
        l_line_center = argmax_between(strip_scores, l_search_min, l_search_max)

        # Find the best right centroid nearby the centroid from the row below
        r_search_min = int(max(r_line_center - margin, 0))
        r_search_max = int(min(r_line_center + margin, img_w))
        r_line_center = argmax_between(strip_scores, r_search_min, r_search_max)

        # Override with last center if search area had no pixels
        if strip_scores[l_line_center] == 0:
            l_line_center = window_lr_centroids[-1][0]
        if strip_scores[r_line_center] == 0:
            r_line_center = window_lr_centroids[-1][1]

        window_lr_centroids.append((l_line_center, r_line_center))

    return window_lr_centroids


def score_columns(image, window_width):
    assert window_width % 2 != 0, 'window_width must be odd'
    window = np.ones(window_width)
    col_sums = np.sum(image, axis=0)
    scores = center_convolve(col_sums, window, mode='constant')
    return scores


def argmax_between(arr, begin, end):
    return np.argmax(arr[begin:end]) + begin


def find_lane(dashcam_img, cam_matrix, distortion_coeffs, subplots=None):
    # Undistort
    undistorted_img = cv2.undistort(dashcam_img, cam_matrix, distortion_coeffs, None, cam_matrix)

    # Change color space
    hls = cv2.cvtColor(dashcam_img, cv2.COLOR_BGR2HLS)
    lightness = hls[:, :, 1]
    saturation = hls[:, :, 2]

    # Transform
    transformed_lightness = transform_to_overhead(lightness)
    transformed_saturation = transform_to_overhead(saturation)

    # Threshold for lanes
    lightness_binary = threshold_lanes(transformed_lightness)
    saturation_binary = threshold_lanes(transformed_saturation)

    # Stack binary images
    combo_binary = lightness_binary + saturation_binary

    # Select lane lines
    window_width = 41
    window_height = 100
    margin = 50
    window_centroids = find_window_centroids(combo_binary, window_width=window_width, window_height=window_height,
                                             margin=margin)
    # Print out everything
    if subplots is not None:
        subplots.imshow(undistorted_img, "Undistorted Road")
        subplots.imshow(lightness, "Lightness Image", cmap='gray')
        subplots.imshow(transformed_lightness, "Overhead Lightness", cmap='gray')
        subplots.imshow(lightness_binary, "Binary Lightness", cmap='gray')
        subplots.skip_plot()
        subplots.imshow(saturation, "Saturation Image", cmap='gray')
        subplots.imshow(transformed_saturation, "Overhead Saturation", cmap='gray')
        subplots.imshow(saturation_binary, "Binary Saturation", cmap='gray')
        subplots.imshow(combo_binary, "Binary Combined", cmap='gray')
        centroids_img = overlay_centroids(combo_binary, window_centroids, window_height, window_width)
        subplots.imshow(centroids_img, "Centroids")


if __name__ == '__main__':
    # Calibrate using checkerboard
    calib_imgs = glob.glob('./camera_cal/*.jpg')
    example_img = cv2.imread(calib_imgs[0])
    img_size = (example_img.shape[1], example_img.shape[0])
    objpoints, imgpoints = find_object_img_points(calib_imgs, 9, 6)
    camera_matrix, dist_coeffs = calibrate(objpoints, imgpoints, img_size)

    # Run pipeline on test images
    test_imgs = glob.glob('./test_images/*.jpg')
    for imgf in test_imgs[:]:
        subplots = DynamicSubplot(3, 4)
        img = plt.imread(imgf)
        find_lane(img, camera_matrix, dist_coeffs, subplots=subplots)

    # Show all plots
    plt.show()
