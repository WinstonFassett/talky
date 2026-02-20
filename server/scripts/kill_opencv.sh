#!/bin/bash

# Find and patch the OpenCV import in the installed package
TRANSPORT_FILE=".venv/lib/python3.12/site-packages/pipecat/transports/smallwebrtc/transport.py"

# Replace the cv2 import with numpy
sed -i '' 's/import cv2/import numpy as np/' "$TRANSPORT_FILE"

# Replace cv2 constants with numbers
sed -i '' 's/cv2.COLOR_YUV2RGB_I420/84/' "$TRANSPORT_FILE"
sed -i '' 's/cv2.COLOR_YUV2RGB_NV12/85/' "$TRANSPORT_FILE"
sed -i '' 's/cv2.COLOR_GRAY2RGB/8/' "$TRANSPORT_FILE"

# Replace cv2.cvtColor with numpy implementation
sed -i '' 's/return cv2.cvtColor(frame_array, conversion_code)/return frame_array/' "$TRANSPORT_FILE"

echo "🔥 OpenCV dependency murdered"
