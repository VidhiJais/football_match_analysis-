from ultralytics import YOLO
import supervision as sv
import pickle
import os
import numpy as np
import pandas as pd
import cv2
import sys 
sys.path.append('../')
from utils import get_center_of_bbox, get_bbox_width, get_foot_position

class Tracker:
    def __init__(self, model_path):
        self.model = YOLO(model_path) # Load YOLO model for object detection
        self.tracker = sv.ByteTrack() # ByteTrack for tracking detected objects
        self.max_width = 1280  # Set maximum width for resizing
        self.max_height = 720  # Set maximum height for resizing

    def add_position_to_tracks(sekf,tracks):
        for object, object_tracks in tracks.items():
            for frame_num, track in enumerate(object_tracks):
                for track_id, track_info in track.items():
                    bbox = track_info['bbox']
                    if object == 'ball':
                        position= get_center_of_bbox(bbox)
                    else:
                        position = get_foot_position(bbox)
                    tracks[object][frame_num][track_id]['position'] = position

    def interpolate_ball_positions(self,ball_positions):
        ball_positions = [x.get(1,{}).get('bbox',[]) for x in ball_positions]
        df_ball_positions = pd.DataFrame(ball_positions,columns=['x1','y1','x2','y2'])

        # Interpolate missing values
        df_ball_positions = df_ball_positions.interpolate()
        df_ball_positions = df_ball_positions.bfill()

        ball_positions = [{1: {"bbox":x}} for x in df_ball_positions.to_numpy().tolist()]

        return ball_positions

    def detect_frames(self, frames):
        batch_size=20 
        detections = []
        for i in range(0,len(frames),batch_size): # 0 ++20 ++20 ... frame length
            detections_batch = self.model.predict(frames[i:i+batch_size],conf=0.1)
            detections += detections_batch
        return detections

    def get_object_tracks(self, frames, read_from_stub=False, stub_path=None):
        
        if read_from_stub and stub_path is not None and os.path.exists(stub_path):
            with open(stub_path,'rb') as f:
                tracks = pickle.load(f)
            return tracks

        detections = self.detect_frames(frames)

        tracks={
            "players":[],
            "referees":[],
            "ball":[]
        }

        for frame_num, detection in enumerate(detections):
            cls_names = detection.names
            cls_names_inv = {v:k for k,v in cls_names.items()}

            # Covert to supervision Detection format
            detection_supervision = sv.Detections.from_ultralytics(detection)

            # Convert GoalKeeper to player object
            for object_ind , class_id in enumerate(detection_supervision.class_id):
                if cls_names[class_id] == "goalkeeper":
                    detection_supervision.class_id[object_ind] = cls_names_inv["player"]

            # Track Objects
            detection_with_tracks = self.tracker.update_with_detections(detection_supervision)

            tracks["players"].append({})
            tracks["referees"].append({})
            tracks["ball"].append({})

            for frame_detection in detection_with_tracks:
                bbox = frame_detection[0].tolist()
                cls_id = frame_detection[3]
                track_id = frame_detection[4]

                if cls_id == cls_names_inv['player']:
                    tracks["players"][frame_num][track_id] = {"bbox":bbox}
                
                if cls_id == cls_names_inv['referee']:
                    tracks["referees"][frame_num][track_id] = {"bbox":bbox}
            
            for frame_detection in detection_supervision:
                bbox = frame_detection[0].tolist()
                cls_id = frame_detection[3]

                if cls_id == cls_names_inv['ball']:
                    tracks["ball"][frame_num][1] = {"bbox":bbox}

        if stub_path is not None:
            with open(stub_path,'wb') as f:
                pickle.dump(tracks,f)

        return tracks
    
    def draw_ellipse(self,frame,bbox,color,track_id=None):
        y2 = int(bbox[3])
        x_center, _ = get_center_of_bbox(bbox)
        width = get_bbox_width(bbox)

        cv2.ellipse(
            frame,
            center=(x_center,y2),
            axes=(int(width), int(0.35*width)),
            angle=0.0,
            startAngle=-45,
            endAngle=235,
            color = color,
            thickness=2,
            lineType=cv2.LINE_4
        )

        rectangle_width = 40
        rectangle_height=20
        x1_rect = x_center - rectangle_width//2
        x2_rect = x_center + rectangle_width//2
        y1_rect = (y2- rectangle_height//2) +15
        y2_rect = (y2+ rectangle_height//2) +15

        if track_id is not None:
            cv2.rectangle(frame,
                          (int(x1_rect),int(y1_rect) ),
                          (int(x2_rect),int(y2_rect)),
                          color,
                          cv2.FILLED)
            
            x1_text = x1_rect+12
            if track_id > 99:
                x1_text -=10
            
            cv2.putText(
                frame,
                f"{track_id}",
                (int(x1_text),int(y1_rect+15)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0,0,0),
                2
            )

        return frame

    def draw_traingle(self,frame,bbox,color):
        y= int(bbox[1])
        x,_ = get_center_of_bbox(bbox)

        triangle_points = np.array([
            [x,y],
            [x-10,y-20],
            [x+10,y-20],
        ])
        cv2.drawContours(frame, [triangle_points],0,color, cv2.FILLED)
        cv2.drawContours(frame, [triangle_points],0,(0,0,0), 2)

        return frame
    
    def resize_frame(self, frame):
        # Resizes the frame if it exceeds the maximum allowed dimensions.
        height, width = frame.shape[:2]
        if width > self.max_width or height > self.max_height:
            # Calculate the scaling factor while maintaining aspect ratio
            scaling_factor = min(self.max_width / width, self.max_height / height)
            new_size = (int(width * scaling_factor), int(height * scaling_factor))
            frame = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)
        return frame

    def draw_team_ball_control(self, frame, frame_num, team_ball_control):
        # Resize frame if needed
        frame = self.resize_frame(frame)
        print(frame.shape)

        # Get current frame dimensions
        frame_height, frame_width = frame.shape[:2]

        # Calculate relative positions based on frame dimensions
        overlay_start_x = int(frame_width * 0.7)  # 70% of the frame width
        overlay_start_y = int(frame_height * 0.85)  # 85% of the frame height
        overlay_end_x = overlay_start_x + int(frame_width * 0.3)  # Covering 30% width
        overlay_end_y = overlay_start_y + int(frame_height * 0.1)  # Covering 10% height

        # Draw semi-transparent rectangle
        overlay = frame.copy()
        cv2.rectangle(overlay, (overlay_start_x, overlay_start_y), (overlay_end_x, overlay_end_y), (255, 255, 255), -1)
        alpha = 0.4
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

        # Calculate ball control percentages
        team_ball_control_till_frame = team_ball_control[:frame_num + 1]
        team_1_frames = (team_ball_control_till_frame == 1).sum()
        team_2_frames = (team_ball_control_till_frame == 2).sum()
        
        # Safely calculate percentages to avoid division by zero
        total_frames = team_1_frames + team_2_frames
        team_1_percentage = (team_1_frames / total_frames) * 100 if total_frames > 0 else 0
        team_2_percentage = (team_2_frames / total_frames) * 100 if total_frames > 0 else 0

        # Place ball control text in the overlay
        text_y = overlay_start_y + int(frame_height * 0.03)  # 3% of frame height as padding
        cv2.putText(frame, f"Team 1 Ball Control: {team_1_percentage:.2f}%", (overlay_start_x + 10, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
        cv2.putText(frame, f"Team 2 Ball Control: {team_2_percentage:.2f}%", (overlay_start_x + 10, text_y + 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

        return frame

    def draw_annotations(self, video_frames, tracks, team_ball_control):
        output_video_frames = []
        
        for frame_num, frame in enumerate(video_frames):
            frame = frame.copy()

            # Check if the frame_num exists in each track type to avoid IndexError
            if frame_num < len(tracks["players"]):
                player_dict = tracks["players"][frame_num]
            else:
                player_dict = {}

            if frame_num < len(tracks["ball"]):
                ball_dict = tracks["ball"][frame_num]
            else:
                ball_dict = {}

            if frame_num < len(tracks["referees"]):
                referee_dict = tracks["referees"][frame_num]
            else:
                referee_dict = {}

            # Draw Players
            for track_id, player in player_dict.items():
                color = player.get("team_color", (0, 0, 255))
                frame = self.draw_ellipse(frame, player["bbox"], color, track_id)

                if player.get('has_ball', False):
                    frame = self.draw_traingle(frame, player["bbox"], (0, 0, 255))

            # Draw Referees
            for _, referee in referee_dict.items():
                frame = self.draw_ellipse(frame, referee["bbox"], (0, 255, 255))

            # Draw Ball
            for track_id, ball in ball_dict.items():
                frame = self.draw_traingle(frame, ball["bbox"], (0, 255, 0))

            # Draw Team Ball Control
            frame = self.draw_team_ball_control(frame, frame_num, team_ball_control)

            output_video_frames.append(frame)

        return output_video_frames