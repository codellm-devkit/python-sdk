# Image for a Python 3 development environment
FROM python:3.11-bookworm

# Turn off interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Add any Python tools that are needed beyond Python 3.11
RUN apt-get update && \
    apt-get install -y sudo build-essential lsb-release software-properties-common ca-certificates \
        python3.11-dev python3.11-venv libxmlsec1-dev gnupg gcc vim make git zip tree curl wget jq

# Add LLVM for C support with symlinks
RUN wget https://apt.llvm.org/llvm.sh && \
    chmod +x llvm.sh && \
    ./llvm.sh 18 && \
    apt-get install -y llvm-18 llvm-18-dev clang-18 libclang-18-dev && \
    ln -s /usr/bin/clang-18 /usr/bin/clang && \
    ln -s /usr/bin/llvm-config-18 /usr/bin/llvm-config

# Install Ollama
# RUN curl -fsSL https://ollama.com/install.sh | sh

# Create a user for development
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID

# Create the user with passwordless sudo privileges
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME -s /bin/bash \
    && usermod -aG sudo $USERNAME \
    && echo $USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

# Set up the Python development environment
WORKDIR /python-sdk
RUN python3 -m pip install --upgrade pip wheel && \
    pip3 install uv

# Enable color terminal for docker exec bash
ENV TERM=xterm-256color

# Become a regular user
USER $USERNAME

# Add Java dependencies with SDKMan as a regular user
ARG HOME="/home/$USERNAME"
RUN curl -s "https://get.sdkman.io" | bash
# this SHELL command is needed to allow using source
SHELL ["/bin/bash", "-c"]    
RUN source "$HOME/.sdkman/bin/sdkman-init.sh" && \
    sdk install java 11.0.25-sem && \
    sdk use java 11.0.25-sem && \
    sdk install maven

# Add Rust support
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup.sh && \
    sh /tmp/rustup.sh -y && \
    rm /tmp/rustup.sh
