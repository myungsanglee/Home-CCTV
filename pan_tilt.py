import smbus
from pca9685 import PCA9685, CHANNEL00, CHANNEL01
from servo_pca9685 import ServoPCA9685

from pynput import keyboard

class PanTiltServo:
    def __init__(self):
        i2cBus = smbus.SMBus(1)
        pca9685 = PCA9685(i2cBus)
        self.servo00 = ServoPCA9685(pca9685, CHANNEL00) # Tilt Servo
        self.servo01 = ServoPCA9685(pca9685, CHANNEL01) # Pan Servo
        self.tilt_angle = 90
        self.pan_angle = 90
        self.servo00.set_angle(self.tilt_angle)
        self.servo01.set_angle(self.pan_angle)
    
    def get_tilt_angle(self):
        return self.tilt_angle
    
    def set_tilt_angle(self, angle):
        self.servo00.set_angle(angle)
        self.tilt_angle = angle
    
    def get_pan_angle(self):
        return self.pan_angle
    
    def set_pan_angle(self, angle):
        self.servo01.set_angle(angle)
        self.pan_angle = angle
        
        
if __name__ == '__main__':
    pan_tilt_servo = PanTiltServo()
    
    def on_press(key):
        try:
            print(f'key pressed: {key.char} ')
        except AttributeError:
            print(f'special key pressed: {key}')

    def on_release(key):
        print(f'Key released: {key}')
        if key == keyboard.Key.esc:
            # Stop listener
            return False
        elif key == keyboard.Key.right:
            angle = min(180, pan_tilt_servo.get_pan_angle() + 2)
            pan_tilt_servo.set_pan_angle(angle)
        elif key == keyboard.Key.left:
            angle = max(0, pan_tilt_servo.get_pan_angle() - 2)
            pan_tilt_servo.set_pan_angle(angle)
        elif key == keyboard.Key.up:
            angle = min(180, pan_tilt_servo.get_tilt_angle() + 2)
            pan_tilt_servo.set_tilt_angle(angle)
        elif key == keyboard.Key.down:
            angle = max(0, pan_tilt_servo.get_tilt_angle() - 2)
            pan_tilt_servo.set_tilt_angle(angle)

    # Collect events until released
    with keyboard.Listener(
            on_press=on_press,
            on_release=on_release) as listener:
        listener.join()