#!/bin/bash

# check if python env 'env' exists, if not create it
if [ ! -d "env" ]; then
    python3 -m venv env
fi

# activate the python env
source env/bin/activate

# install the required packages
#pip install -r requirements.txt