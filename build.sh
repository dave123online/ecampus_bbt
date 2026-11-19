#!/usr/bin/env bash
# Build Render : dépendances + fichiers statiques.
# Les migrations tournent dans preDeployCommand (render.yaml), avec accès DB.
set -o errexit

pip install --upgrade pip
pip install -r requirements.txt
python manage.py collectstatic --no-input
