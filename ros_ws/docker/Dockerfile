FROM ros:jazzy-ros-base-noble

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Jazzy's ros_gz packages supply the matching Harmonic vendor libraries.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ros-jazzy-ros-gz \
    ros-jazzy-robot-state-publisher \
    ros-jazzy-xacro \
    ros-jazzy-navigation2 \
    ros-jazzy-nav2-bringup \
    ros-jazzy-slam-toolbox \
    ros-jazzy-rviz2 \
    python3-venv \
    nodejs \
    xvfb x11vnc x11-utils fluxbox novnc websockify \
    libgl1-mesa-dri mesa-utils \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1001 --shell /bin/bash rescuebot \
    && mkdir -p /workspace \
    && chown rescuebot:rescuebot /workspace

WORKDIR /workspace
COPY --chown=rescuebot:rescuebot pyproject.toml ai_camera_detect.py ./
COPY --chown=rescuebot:rescuebot app/ app/
COPY --chown=rescuebot:rescuebot fixtures/ fixtures/
COPY --chown=rescuebot:rescuebot tests/ tests/
COPY --chown=rescuebot:rescuebot tools/sensors/ tools/sensors/
COPY --chown=rescuebot:rescuebot ros_ws/src/ ros_ws/src/
COPY --chown=rescuebot:rescuebot ros_ws/docker/ ros_ws/docker/

RUN python3 -m venv --system-site-packages /opt/rescuebot-venv \
    && /opt/rescuebot-venv/bin/pip install --no-cache-dir -e .

# Colcon console scripts use the system ROS Python; make the application's
# pure IPC modules available to those scripts as well as the dashboard venv.
ENV PATH="/opt/rescuebot-venv/bin:${PATH}" \
    PYTHONPATH="/workspace/app" \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    XDG_RUNTIME_DIR=/tmp/rescuebot-runtime \
    LIBGL_ALWAYS_SOFTWARE=1 \
    QT_X11_NO_MITSHM=1 \
    ROS_DOMAIN_ID=86 \
    ROS_LOCALHOST_ONLY=1

USER rescuebot
RUN source /opt/ros/jazzy/setup.bash \
    && cd ros_ws \
    && colcon build --symlink-install --event-handlers console_direct+

EXPOSE 8000 6080
ENTRYPOINT ["/bin/bash", "/workspace/ros_ws/docker/entrypoint.sh"]
CMD ["/bin/bash", "/workspace/ros_ws/docker/start.sh"]
