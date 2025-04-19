#!/usr/bin/env python

from setuptools import setup, find_packages

setup(
    name="webex-bot",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "requests",
        "websocket-client",
        "webexteamssdk",  # Or your forked SDK if applicable
    ],
    author="Mike Allen",
    author_email="your@email.com",
    description="Custom Webex Bot Framework with WebSocket Fix",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/mallen012/webex_bot",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.7",
)
