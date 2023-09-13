# mesh_extract

This repo contains code for a tool for extracting MeSH tags from PubMed documents

## Example usage

The main script that does all the work is extractMeSHFromPubMed.py. To run it on a PubMed file, use the command below. It will output a gzipped JSON lines document with MeSH extracted for every document.

```
python extractMeSHFromPubMed.py --inURL ftp://ftp.ncbi.nlm.nih.gov/pubmed/updatefiles/pubmed23n1456.xml.gz --outFile pubmed23n1456.jsonl.gz
```

## Running across PubMed

To run all of PubMed, use the command below

```
# Creates a file listing all the PubMed files
bash updatePubmedListing.sh

# Runs extractMeSHFromPubMed.py against all of them
bash runAll.sh
```

