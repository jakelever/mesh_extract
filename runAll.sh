#!/bin/bash
set -ex

mkdir -p output

while read URL
do
	FILENAME=$(echo $URL | awk -F '/' ' { print $NF } ' | cut -f 1 -d '.')
	FILENAME=output/$FILENAME.jsonl.gz

	if [ -f $FILENAME ]; then
		continue
	fi

	python extractMeSHFromPubMed.py --inURL $URL --outFile $FILENAME
	
done < pubmed_listing.txt

