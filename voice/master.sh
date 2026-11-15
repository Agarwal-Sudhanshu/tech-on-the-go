#!/bin/sh
# Clean up and loudness-master a voice track: master.sh in.wav out.wav
# - removes steady background noise (fan hiss) and low rumble
# - quietens the gaps between sentences
# - evens out level and raises it to YouTube's loudness target (-14 LUFS)
set -e
ffmpeg -hide_banner -loglevel error -y -i "$1" -af "\
highpass=f=85,\
afftdn=nr=24:nf=-38:tn=1,\
agate=threshold=0.012:ratio=3:attack=10:release=250,\
acompressor=threshold=-20dB:ratio=3:attack=8:release=160:makeup=2,\
loudnorm=I=-14:TP=-1.5:LRA=9" -ar 48000 -ac 1 "$2"
