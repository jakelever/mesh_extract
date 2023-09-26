import argparse
import json
import xml.etree.cElementTree as etree
import html
import re
import calendar
import unicodedata
import sys

import tempfile
import hashlib

import shutil
import urllib.request as request
from contextlib import closing
import time
import gzip
import traceback
import jsonlines

def download_file(url,local_filehandle):
	with closing(request.urlopen(url)) as r:
		shutil.copyfileobj(r, local_filehandle)

def download_file_and_check_md5sum(url, local_filehandle):
	with tempfile.NamedTemporaryFile() as tf:
		md5_url = "%s.md5" % url
		download_file(md5_url, tf.file)
		
		tf.file.seek(0)
		expected_md5 = tf.file.read().decode().strip()
		assert expected_md5.startswith('MD5(') and '=' in expected_md5
		expected_md5 = expected_md5.split('=')[1].strip()
		print("expected MD5:", expected_md5)

	download_file(url, local_filehandle)
	local_filehandle.seek(0)
	got_md5 = hashlib.md5(local_filehandle.read()).hexdigest()
	print("got MD5:", got_md5)

	if expected_md5 != got_md5:
		raise RuntimeError("MD5 of downloaded file doesn't match expected: %s != %s" % (expected_md5,got_md5))

def download_file_with_retries(url, local_filehandle, check_md5=False, retries=10):
	for tryno in range(retries):
		try:
			if check_md5:
				download_file_and_check_md5sum(url, local_filehandle)
			else:
				download_file(url,local_filehandle)
			return
		except:
			print("Unexpected error:", sys.exc_info()[0], sys.exc_info()[1])
			traceback.print_exc()
			time.sleep(5*(tryno+1))

	raise RuntimeError("Unable to download %s" % url)

# Remove empty brackets (that could happen if the contents have been removed already
# e.g. for citation ( [3] [4] ) -> ( ) -> nothing
def removeBracketsWithoutWords(text):
	fixed = re.sub(r'\([\W\s]*\)', ' ', text)
	fixed = re.sub(r'\[[\W\s]*\]', ' ', fixed)
	fixed = re.sub(r'\{[\W\s]*\}', ' ', fixed)
	return fixed

# Some older articles have titles like "[A study of ...]."
# This removes the brackets while retaining the full stop
def removeBracketsFromTitles(titleText):
	titleText = titleText.strip()
	if titleText[0] == '[' and titleText[-2:] == '].':
		titleText = titleText[1:-2] + '.'
	return titleText

def cleanupText(text):
	# Remove some "control-like" characters (left/right separator)
	text = text.replace(u'\u2028',' ').replace(u'\u2029',' ')
	text = "".join(ch for ch in text if unicodedata.category(ch)[0]!="C")
	text = "".join(ch if unicodedata.category(ch)[0]!="Z" else " " for ch in text)

	# Remove repeated commands and commas next to periods
	text = re.sub(',(\s*,)*',',',text)
	text = re.sub('(,\s*)*\.','.',text)
	return text.strip()

# XML elements to ignore the contents of
ignoreList = ['table', 'table-wrap', 'xref', 'disp-formula', 'inline-formula', 'ref-list', 'bio', 'ack', 'graphic', 'media', 'tex-math', 'mml:math', 'object-id', 'ext-link']


# XML elements to separate text between
separationList = ['title', 'p', 'sec', 'break', 'def-item', 'list-item', 'caption']
def extractTextFromElem(elem):
	# Extract any raw text directly in XML element or just after
	head = ""
	if elem.text:
		head = elem.text
	tail = ""
	if elem.tail:
		tail = elem.tail
	
	# Then get the text from all child XML nodes recursively
	childText = []
	for child in elem:
		childText = childText + extractTextFromElem(child)
		
	# Check if the tag should be ignore (so don't use main contents)
	if elem.tag in ignoreList:
		return [tail.strip()]
	# Add a zero delimiter if it should be separated
	elif elem.tag in separationList:
		return [0] + [head] + childText + [tail]
	# Or just use the whole text
	else:
		return [head] + childText + [tail]
	

# Merge a list of extracted text blocks and deal with the zero delimiter
def extractTextFromElemList_merge(list):
	textList = []
	current = ""
	# Basically merge a list of text, except separate into a new list
	# whenever a zero appears
	for t in list:
		if t == 0: # Zero delimiter so split
			if len(current) > 0:
				textList.append(current)
				current = ""
		else: # Just keep adding
			current = current + " " + t
			current = current.strip()
	if len(current) > 0:
		textList.append(current)
	return textList
	
# Main function that extracts text from XML element or list of XML elements
def extractTextFromElemList(elemList):
	textList = []
	# Extracts text and adds delimiters (so text is accidentally merged later)
	if isinstance(elemList, list):
		for e in elemList:
			textList = textList + extractTextFromElem(e) + [0]
	else:
		textList = extractTextFromElem(elemList) + [0]

	# Merge text blocks with awareness of zero delimiters
	mergedList = extractTextFromElemList_merge(textList)
	
	# Remove any newlines (as they can be trusted to be syntactically important)
	mergedList = [ text.replace('\n', ' ') for text in mergedList ]

	# Remove no-break spaces
	mergedList = [ cleanupText(text) for text in mergedList ]
	
	return mergedList

def getJournalDateForMedlineFile(elem,pmid):
	yearRegex = re.compile(r'(18|19|20)\d\d')

	monthMapping = {}
	for i,m in enumerate(calendar.month_name):
		monthMapping[m] = i
	for i,m in enumerate(calendar.month_abbr):
		monthMapping[m] = i

	# Try to extract the publication date
	pubDateField = elem.find('./MedlineCitation/Article/Journal/JournalIssue/PubDate')
	medlineDateField = elem.find('./MedlineCitation/Article/Journal/JournalIssue/PubDate/MedlineDate')

	assert not pubDateField is None, "Couldn't find PubDate field for PMID=%s" % pmid

	medlineDateField = pubDateField.find('./MedlineDate')
	pubDateField_Year = pubDateField.find('./Year')
	pubDateField_Month = pubDateField.find('./Month')
	pubDateField_Day = pubDateField.find('./Day')

	pubYear,pubMonth,pubDay = None,None,None
	if not medlineDateField is None:
		regexSearch = re.search(yearRegex,medlineDateField.text)
		if regexSearch:
			pubYear = regexSearch.group()
		monthSearch = [ c for c in (list(calendar.month_name) + list(calendar.month_abbr)) if c != '' and c in medlineDateField.text ]
		if len(monthSearch) > 0:
			pubMonth = monthSearch[0]
	else:
		if not pubDateField_Year is None:
			pubYear = pubDateField_Year.text
		if not pubDateField_Month is None:
			pubMonth = pubDateField_Month.text
		if not pubDateField_Day is None:
			pubDay = pubDateField_Day.text

	if not pubYear is None:
		pubYear = int(pubYear)
		if not (pubYear > 1700 and pubYear < 2100):
			pubYear = None

	if not pubMonth is None:
		if pubMonth in monthMapping:
			pubMonth = monthMapping[pubMonth]
		pubMonth = int(pubMonth)
	if not pubDay is None:
		pubDay = int(pubDay)

	return pubYear,pubMonth,pubDay

def getPubmedEntryDate(elem,pmid):
	pubDateFields = elem.findall('./PubmedData/History/PubMedPubDate')
	allDates = {}
	for pubDateField in pubDateFields:
		assert 'PubStatus' in pubDateField.attrib
		#if 'PubStatus' in pubDateField.attrib and pubDateField.attrib['PubStatus'] == "pubmed":
		pubDateField_Year = pubDateField.find('./Year')
		pubDateField_Month = pubDateField.find('./Month')
		pubDateField_Day = pubDateField.find('./Day')
		pubYear = int(pubDateField_Year.text)
		pubMonth = int(pubDateField_Month.text)
		pubDay = int(pubDateField_Day.text)

		dateType = pubDateField.attrib['PubStatus']
		if pubYear > 1700 and pubYear < 2100:
			allDates[dateType] = (pubYear,pubMonth,pubDay)

	if len(allDates) == 0:
		return None,None,None

	if 'pubmed' in allDates:
		pubYear,pubMonth,pubDay = allDates['pubmed']
	elif 'entrez' in allDates:
		pubYear,pubMonth,pubDay = allDates['entrez']
	elif 'medline' in allDates:
		pubYear,pubMonth,pubDay = allDates['medline']
	else:
		pubYear,pubMonth,pubDay = list(allDates.values())[0]

	return pubYear,pubMonth,pubDay

def extract_mesh_from_pubmed(inFile,outFile):

	with gzip.open(outFile,'wt') as outF:
		writer = jsonlines.Writer(outF)

		for event, elem in etree.iterparse(inFile, events=('start', 'end', 'start-ns', 'end-ns')):
			if (event=='end' and elem.tag=='PubmedArticle'): #MedlineCitation'):
				pmidField = elem.find('./MedlineCitation/PMID')
				pmid = int(pmidField.text)
				
				titleElems = elem.findall('./MedlineCitation/Article/ArticleTitle')
				assert len(titleElems) == 1
				title = titleElems[0].text

				language = elem.find('./MedlineCitation/Article/Language').text

				journalYear,journalMonth,journalDay = getJournalDateForMedlineFile(elem,pmid)
				entryYear,entryMonth,entryDay = getPubmedEntryDate(elem,pmid)

				jComparison = tuple ( 9999 if d is None else d for d in [ journalYear,journalMonth,journalDay ] )
				eComparison = tuple ( 9999 if d is None else d for d in [ entryYear,entryMonth,entryDay ] )
				if jComparison < eComparison: # The PubMed entry has been delayed for some reason so let's try the journal data
					pubYear,pubMonth,pubDay = journalYear,journalMonth,journalDay
				else:
					pubYear,pubMonth,pubDay = entryYear,entryMonth,entryDay

				mesh_headings = []
				mesh_elems = elem.findall("./MedlineCitation/MeshHeadingList/MeshHeading")
				for mesh_elem in mesh_elems:
					descriptor_elem = mesh_elem.find("./DescriptorName")
					mesh_id = descriptor_elem.attrib["UI"]
					major_topic_yn = descriptor_elem.attrib["MajorTopicYN"]
					name = descriptor_elem.text

					mesh_heading = {'name':name, 'id':mesh_id, 'is_major': major_topic_yn, 'qualifiers':[]}

					qualifier_elems = mesh_elem.findall("./QualifierName")
					for qualifier_elem in qualifier_elems:
						mesh_id = qualifier_elem.attrib["UI"]
						major_topic_yn = qualifier_elem.attrib["MajorTopicYN"]
						name = qualifier_elem.text

						qualifier = {'name':name, 'id':mesh_id, 'is_major': major_topic_yn}
						mesh_heading['qualifiers'].append(qualifier)

					mesh_headings.append(mesh_heading)
					
				journal_title_fields = elem.findall("./MedlineCitation/Article/Journal/Title")
				journal_title_iso_fields = elem.findall(
					"./MedlineCitation/Article/Journal/ISOAbbreviation"
				)

				journal_title, journal_iso_title = "", ""
				assert len(journal_title_fields) <= 1, "Error with pmid=%s" % pmid
				assert len(journal_title_iso_fields) <= 1, "Error with pmid=%s" % pmid
				if journal_title_fields:
					journal_title = journal_title_fields[0].text
				if journal_title_iso_fields:
					journal_iso_title = journal_title_iso_fields[0].text
					
				document = {'pmid':pmid, 'publication_date':(pubYear,pubMonth,pubDay), 'journal':journal_title, 'journal_abbrev':journal_iso_title, 'mesh':mesh_headings}
				writer.write(document)
				
				# Important: clear the current element from memory to keep memory usage low
				elem.clear()

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Extract MeSH terms from a PubMed file given its URL")
	parser.add_argument('--inURL',required=True,type=str,help='Input PubMed file URL on FTP')
	parser.add_argument('--outFile',required=True,type=str,help='Output gzipped JSONL filtered PubMed file')
	args = parser.parse_args()
	
	with tempfile.NamedTemporaryFile() as tf_pubmed_gz, tempfile.NamedTemporaryFile() as tf_pubmed:
		print("Downloading...")
		download_file_with_retries(args.inURL, tf_pubmed_gz.file, check_md5=True)
				
		tf_pubmed_gz.file.seek(0)
		gzippedFile = gzip.GzipFile(fileobj=tf_pubmed_gz.file)
			
		print("Extracting MeSH from Pubmed file...")
		extract_mesh_from_pubmed(gzippedFile, args.outFile)
		
		print("Done.")
