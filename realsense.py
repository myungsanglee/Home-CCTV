import numpy as np
import pyrealsense2 as rs


class RealSense:
    def __init__(self):
        self.pipeline = rs.pipeline()
        config = rs.config()
        # config.enable_stream(rs.stream.color, 1920, 1080, rs.format.bgr8, 30)
        config.enable_stream(rs.stream.color, 1280, 720, rs.format.bgr8, 30)
        # config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)

        self.pipeline.start(config)

    def __del__(self):
        self.pipeline.stop()

    def get_numpy_color_image(self):
        frames = self.pipeline.wait_for_frames()
        frames = frames.get_color_frame()
        frames = np.asanyarray(frames.get_data())

        return frames