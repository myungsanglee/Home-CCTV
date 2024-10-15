import time
from adafruit_pca9685 import PCA9685
from board import SCL, SDA
import busio

MIN_PULSE = 150  # 최소 펄스 길이 (0도, 마이크로초 단위)
MAX_PULSE = 600  # 최대 펄스 길이 (180도, 마이크로초 단위)

class PanTiltServoV2:
    def __init__(self):
        i2c = busio.I2C(SCL, SDA)
        time.sleep(1)
        self.pca = PCA9685(i2c)
        self.pca.frequency = 60
        self.tilt_angle = 100
        self.pan_angle = 90
        self.set_tilt_angle(self.tilt_angle)
        self.set_pan_angle(self.pan_angle)
    
    def get_tilt_angle(self):
        return self.tilt_angle
    
    def set_tilt_angle(self, angle):
        pulse = MIN_PULSE + (angle / 180.0) * (MAX_PULSE - MIN_PULSE)
        duty_cycle = int((pulse / 4096.0) * 0xFFFF)
        self.pca.channels[0].duty_cycle = duty_cycle
        time.sleep(0.03)
        self.tilt_angle = angle
    
    def get_pan_angle(self):
        return self.pan_angle
    
    def set_pan_angle(self, angle):
        pulse = MIN_PULSE + (angle / 180.0) * (MAX_PULSE - MIN_PULSE)
        duty_cycle = int((pulse / 4096.0) * 0xFFFF)
        self.pca.channels[1].duty_cycle = duty_cycle
        time.sleep(0.03)
        self.pan_angle = angle










# import smbus
# from pca9685 import PCA9685, CHANNEL00, CHANNEL01
# from servo_pca9685 import ServoPCA9685
# from pynput import keyboard

# class PanTiltServo:
#     def __init__(self):
#         i2cBus = smbus.SMBus(1)
#         time.sleep(1) 
#         pca9685 = PCA9685(i2cBus)
#         self.servo00 = ServoPCA9685(pca9685, CHANNEL00) # Tilt Servo
#         self.servo01 = ServoPCA9685(pca9685, CHANNEL01) # Pan Servo
#         self.tilt_angle = 100
#         self.pan_angle = 90
#         self.servo00.set_angle(self.tilt_angle)
#         self.servo01.set_angle(self.pan_angle)
    
#     def get_tilt_angle(self):
#         return self.tilt_angle
    
#     def set_tilt_angle(self, angle):
#         self.servo00.set_angle(angle)
#         self.tilt_angle = angle
    
#     def get_pan_angle(self):
#         return self.pan_angle
    
#     def set_pan_angle(self, angle):
#         self.servo01.set_angle(angle)
#         self.pan_angle = angle        
        
        
# if __name__ == '__main__':
#     pan_tilt_servo = PanTiltServo()
#     per_angle = 20
    
#     def on_press(key):
#         try:
#             print(f'key pressed: {key.char} ')
#         except AttributeError:
#             print(f'special key pressed: {key}')

#     def on_release(key):
#         print(f'Key released: {key}')
#         if key == keyboard.Key.esc:
#             # Stop listener
#             return False
#         elif key == keyboard.Key.right:
#             angle = min(180, pan_tilt_servo.get_pan_angle() + per_angle)
#             pan_tilt_servo.set_pan_angle(angle)
#         elif key == keyboard.Key.left:
#             angle = max(0, pan_tilt_servo.get_pan_angle() - per_angle)
#             pan_tilt_servo.set_pan_angle(angle)
#         elif key == keyboard.Key.up:
#             angle = min(180, pan_tilt_servo.get_tilt_angle() + per_angle)
#             pan_tilt_servo.set_tilt_angle(angle)
#         elif key == keyboard.Key.down:
#             angle = max(0, pan_tilt_servo.get_tilt_angle() - per_angle)
#             pan_tilt_servo.set_tilt_angle(angle)

#     # Collect events until released
#     with keyboard.Listener(
#             on_press=on_press,
#             on_release=on_release) as listener:
#         listener.join()
