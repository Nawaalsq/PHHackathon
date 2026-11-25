#!/bin/sh
# Vercel build steps, moved out of vercel.json (its buildCommand has a
# 256-character limit and the inline version exceeded it).
set -e
npm --prefix web install
npm --prefix web run build
mkdir -p api/_bundle/clean api/_bundle/results
cp -r rd_engine api/_bundle/
cp -r clean/sample1_dev_seeded api/_bundle/clean/
cp -r results/sample1_dev_seeded api/_bundle/results/
# second demo dataset — dev subset so the lambda stays under the 250 MB limit
cp -r clean/sample2_dev_seeded api/_bundle/clean/
cp -r results/sample2_dev_seeded api/_bundle/results/
cp -r answer_keys api/_bundle/answer_keys
