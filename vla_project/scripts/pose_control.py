import numpy as np
from scipy.spatial.transform import Rotation as R
import time
import argparse

import robosuite as suite
from   robosuite.controllers import load_composite_controller_config
from   robosuite.utils.input_utils import *
from   robosuite.utils.camera_utils import CameraMover

import xml.etree.ElementTree as ET

MAX_FR = 25
# Following thresholds are set to determine if the robot is close enough to the target 
POSITION_THRESHOLD = 0.02  # units meter
ORIENTATION_THRESHOLD = 0.1 
# Maximum time to wait for reaching target position
MAX_EXECUTION_TIME = 5.0  
# x, y, z position. This is sorta imp for VLA since, it expects the image to be captured from a certain position 
DEFAULT_CAMERA_POS = np.array([0.7, 0, 1.6])  

def quat2euler(q: np.ndarray) -> np.ndarray:
    '''
        Convert quaternion to euler angles
    '''
    r = R.from_quat(q)
    return r.as_euler('xyz', degrees=False)

def actual_delta(vec : np.ndarray) -> np.ndarray:
    '''
        Usage: 
        input: [dx, dy, dz, droll, dpitch, dyaw, dgripper] -> [0.1 0 0 0 0 0 0]
        actual_pos_delta = (0.05 - (-0.05))/2 * 0.1 + (0.05 + (-0.05))/2 = 0.005
        actual_ori_delta = (0.5 - (-0.5))/2 * 0 + (0.5 + (-0.5))/2 = 0
        actual_gripper_delta = 0
        final output: [0.005, 0, 0, 0, 0, 0, 0]
    '''
    assert np.all(np.abs(vec) <= 1), "Input vector must be within [-1, 1] range"

    # depending on the controller, these configs may differ
    pos_output_min = -0.05
    pos_output_max = 0.05
    ori_output_min = -0.5
    ori_output_max = 0.5

    pos_vec = vec[:3]
    ori_vec = vec[3:6]
    gripper_vec = [vec[6]]

    actual_pos_delta = (pos_output_max - pos_output_min)/2 * pos_vec + (pos_output_max + pos_output_min)/2
    actual_ori_delta = (ori_output_max - ori_output_min)/2 * ori_vec + (ori_output_max + ori_output_min)/2
    actual_gripper_delta = gripper_vec

    return np.concatenate((actual_pos_delta, actual_ori_delta, actual_gripper_delta))

def is_position_reached(target_delta, current_delta) -> bool:
    """
    Check if norm(target_delta) and norm(current_delta) are within a certain threshold.

    return True or False
    """
    if np.abs(np.linalg.norm(target_delta) - np.linalg.norm(current_delta)) < POSITION_THRESHOLD:
        return True
    else:
        return False

def is_orientation_reached(target_delta, current_delta) -> bool:
    """
    Check if norm(target_delta) and norm(current_delta) are within a certain threshold.

    return True or False
    """
    if np.abs(np.linalg.norm(target_delta) - np.linalg.norm(current_delta)) < ORIENTATION_THRESHOLD:
        return True
    else:
        return False

def denormalize(vec: np.ndarray) -> np.ndarray:
    """
    Denormalize the vector output from VLA 
    """
    pass
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Robot pose control from file')
    parser.add_argument('--pose_file', type=str, help='Path to the poses file', default="poses.txt")
    parser.add_argument('--debug', type=bool, default=False)
    args = parser.parse_args()

    robot = "UR5e"  
    controller_config = load_composite_controller_config(controller="BASIC", robot=[robot])

    env = suite.make(
        env_name = "Lift",
        robots=[robot],
        controller_configs=controller_config,
        gripper_types="default", 
        has_renderer=True,
        has_offscreen_renderer=False,
        ignore_done=True,
        use_camera_obs=False,
        control_freq=20,
    )
    env.reset()

    # get camera specs 
    cam_tree = ET.Element("camera", attrib={"name": "agentview"})
    CAMERA_NAME = cam_tree.get("name")
    camera_mover = CameraMover(
        env=env,
        camera=CAMERA_NAME,
    )
    camera_id = env.sim.model.camera_name2id(CAMERA_NAME)
    env.viewer.set_camera(camera_id=camera_id)
    # get initial camera pose 
    initial_file_camera_pos, initial_file_camera_quat = camera_mover.get_camera_pose()
    
    action_space_dim = env.action_dim # 7
    if args.debug:
        print(f"Controlling {robot} robot with {action_space_dim} DOF")
        robot = env.robots[0]
        robot.print_action_info()

    # Reading from file: this will be replaced with VLA
    poses = []
    with open(args.pose_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            try:
                values = [float(x) for x in line.split()]
                if len(values) == action_space_dim:
                    poses.append(values)
            except ValueError:
                print(f"Skipping invalid line: {line}")
                continue
    print(f"Loaded {len(poses)} test orientations")
    
    try:        
        for pos_idx, action_space in enumerate(poses):
            print(f"\nExecuting movement {pos_idx + 1}/{len(poses)}")
            action_space = np.array(action_space)
            control_space = actual_delta(action_space)  # Convert action space to control space
            if args.debug:
                print(f"Action Space: {action_space}")
                print(f"Control Space: {control_space}")
            initial_state_set = False
            
            while True:
                camera_mover.set_camera_pose(pos=DEFAULT_CAMERA_POS, quat=initial_file_camera_quat)
                # env expects action space, it internally maps the action space -> control space
                observations, reward, done, info = env.step(action_space)
                env.render()

                if not initial_state_set:
                    initial_eef_position = observations['robot0_eef_pos']
                    initial_eef_orientation = observations['robot0_eef_quat']
                    initial_state_set = True
                    start_time = time.time()
                    continue

                eef_position = observations['robot0_eef_pos']
                pos_difference = eef_position - initial_eef_position

                eef_orientation = observations['robot0_eef_quat']
                ori_difference = quat2euler(eef_orientation) - quat2euler(initial_eef_orientation)
                
                if args.debug:
                    print("-------------------------------------------------------------")
                    print(f"Current EEF Position: {np.round(eef_position, 3)}")
                    print(f"Position Difference between current and initial : {np.round(pos_difference, 3)}")
                    print(f"Position Difference that is required: {np.round(control_space[:3], 3)}")
                    print(f"Current EEF Orientation: {np.round(eef_orientation, 3)}")
                    print(f"Orientation Difference between current and initial : {np.round(ori_difference, 3)}")
                    print(f"Orientation Difference that is required: {np.round(control_space[3:6], 3)}")
                    print("-------------------------------------------------------------")

                elapsed_time = time.time() - start_time

                if is_position_reached(control_space[:3], pos_difference) and is_orientation_reached(control_space[3:6], ori_difference):
                    break
                elif elapsed_time > MAX_EXECUTION_TIME:
                    print(f"Timeout reached after {elapsed_time:.2f} seconds")
                    break

    except KeyboardInterrupt:
        print("\nCtrl C pressed so exiting...")
    except Exception as e:
        print(f"Error in outer try block: {e}")

    env.close()