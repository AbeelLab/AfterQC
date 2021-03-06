 #!/usr/bin/env python
 
import os,sys
import re
from optparse import OptionParser
import time
import fastq
import util
import barcodeprocesser
import json
from qualitycontrol import QualityControl
from qcreporter import QCReporter

def getMainName(filename):
    baseName = os.path.basename(filename)
    mainName = baseName.replace(".fastq", "").replace(".fq", "").replace(".gz", "")
    return mainName

def trim(read, front, tail):
    if tail>0:
        #\n will be trimmed, so add it back
        read[1] = read[1][front:-tail]
        read[3] = read[3][front:-tail]
    else:
        read[1] = read[1][front:]
        read[3] = read[3][front:]
        
    return read
    
def hasPolyX(seq, maxPoly, mismatch):
    if(len(seq)<maxPoly):
        return None
    
    polyCount = {}
    polyArray = ("A", "T", "C", "G", "a", "t", "c", "g", "N")
    for poly in polyArray: polyCount[poly] = 0
    
    for x in xrange(len(seq)):
        frontbase = seq[x]
        
        if not frontbase in polyArray:
            return None
        
        if x >= maxPoly:
            tailbase = seq[x-maxPoly]
            polyCount[tailbase] -= 1
        
        polyCount[frontbase] += 1
        if polyCount[frontbase] >= maxPoly - mismatch:
            return frontbase
    return None
    
def minQuality(read):
    qualStr = read[3]
    minQual = 255
    for q in qualStr:
        if minQual > ord(q):
            minQual = ord(q)
    return minQual - 33
    
def lowQualityNum(read, qual):
    qual += 33
    qualStr = read[3]
    lowQualNum = 0
    for q in qualStr:
        if ord(q) < qual:
            lowQualNum += 1
    return lowQualNum
    
def nNumber(read):
    seqStr = read[1]
    nNum = 0
    for s in seqStr:
        if s == 'N':
            nNum += 1
    return nNum

def getOverlap(r, overlap_len):
    ret = []
    ret.append(r[0])
    ret.append(r[1][len(r[1]) - overlap_len:])
    ret.append(r[2])
    ret.append(r[3][len(r[3]) - overlap_len:])
    return ret

def makeDict(opt):
    d = {
        'index2_flag': opt.index2_flag,
        'draw': opt.draw,
        'barcode':opt.barcode ,
        'index1_flag':opt.index1_flag,
        'seq_len_req': opt.seq_len_req,
        'index1_file': opt.index1_file,
        'overlap_output_folder': opt.overlap_output_folder,
        'trim_tail': opt.trim_tail,
        'trim_pair_same': opt.trim_pair_same,
        'poly_size_limit': opt.poly_size_limit,
        'good_output_folder': opt.good_output_folder,
        'debubble_dir': opt.debubble_dir,
        'index2_file': opt.index2_file,
        'qualified_quality_phred': opt.qualified_quality_phred,
        'barcode_flag': opt.barcode_flag,
        'trim_front': opt.trim_front,
        'barcode_verify': opt.barcode_verify,
        'read2_file': opt.read2_file,
        'n_base_limit': opt.n_base_limit,
        'barcode_length': opt.barcode_length,
        'trim_tail2': opt.trim_tail2,
        'unqualified_base_limit': opt.unqualified_base_limit,
        'allow_mismatch_in_poly': opt.allow_mismatch_in_poly,
        'input_dir': opt.input_dir,
        'read1_file': opt.read1_file,
        'read2_flag': opt.read2_flag,
        'store_overlap': opt.store_overlap,
        'debubble': opt.debubble,
        'read1_flag': opt.read1_flag,
        'trim_front2': opt.trim_front2,
        'bad_output_folder': opt.bad_output_folder,
        'qc_only': opt.qc_only,
        'qc_sample': opt.qc_sample,
        'qc_kmer': opt.qc_kmer
    }
    return d
    
########################### seqFilter
class seqFilter:
    
    #opt is an object contains lots of parameters
    def __init__(self, opt):
        self.options = opt
        self.bubbleCircles = {}
        self.bubbleTiles = []
        
        #detect if the input is paired and if it has index files
        if self.options.read2_file != None:
            self.paired = True
        if self.options.index1_file != None:
            self.hasIndex = True

        self.pattern = re.compile(r'\S+\:\d+\:\S+\:\d+\:\d+\:\d+\:\d+')

    def loadBubbleCircles(self):
        bubbleCircleFile = os.path.join(self.options.debubble_dir, "circles.csv")
        if not os.path.exists(bubbleCircleFile):
            return
        with open(bubbleCircleFile) as f:
            rows = f.readlines()
            for row in rows[1:]:
                r = row.split(",")
                x = float(r[0])
                y = float(r[1])
                radius = float(r[2])
                lane = int(r[3])
                tile = int(r[4])
                circle = (x,y,radius,lane,tile)
                if tile not in self.bubbleTiles:
                    self.bubbleTiles.append(tile)
                    self.bubbleCircles[tile]=[]
                self.bubbleCircles[tile].append(circle)
    
    def isInBubble(self, seqInfo):
        #illumina sequence name line format
        #@<instrument>:<run number>:<flowcell ID>:<lane>:<tile_no>:<x-pos>:<y-pos> <read>:<is filtered>:<control number>:<index sequence>

        match = self.pattern.search(seqInfo);
        if not match:
            return False

        items = match.group().split(":")
        if len(items) < 7:
            return False
            
        lane = int(items[3])
        tile_no = items[4]
        tile = int(tile_no[1:])
        x = int(items[5])
        y = int(items[6])
        if tile not in self.bubbleTiles:
            return False
        for circle in self.bubbleCircles[tile]:
            cx = circle[0]
            cy = circle[1]
            cr = circle[2]
            clane = circle[3]
            if clane == lane:
                if (cx-x)*(cx-x) + (cy-y)*(cy-y) < cr*cr:
                    return True
                    
        return False

    def writeReads(self, r1, r2, i1, i2, r1_file, r2_file, i1_file, i2_file, flag):
        if self.options.qc_only:
            return

        if r1!=None and r1_file!=None:
            #add flag into the read name
            if flag!=None:
                r1[0] = "@" + flag + r1[0][1:]
            r1_file.writeLines(r1)

        if r2!=None and r2_file!=None:
            #add flag into the read name
            if flag!=None:
                r2[0] = "@" + flag + r2[0][1:]
            r2_file.writeLines(r2)

        if i1!=None and i1_file!=None:
            #add flag into the read name
            if flag!=None:
                i1[0] = "@" + flag + i1[0][1:]
            i1_file.writeLines(i1)

        if i2!=None and i2_file!=None:
            #add flag into the read name
            if flag!=None:
                i2[0] = "@" + flag + i2[0][1:]
            i2_file.writeLines(i2)

    def run(self):
        if self.options.debubble:
            self.loadBubbleCircles()

        #read1_file is required
        read1_file = fastq.Reader(self.options.read1_file)
        #create a QC folder to contains QC results
        qc_base_folder = os.path.join(os.path.dirname(self.options.read1_file), "QC")
        if not os.path.exists(qc_base_folder):
            os.makedirs(qc_base_folder)
        #QC result of this file/pair
        qc_dir =  os.path.join(qc_base_folder, os.path.basename(self.options.read1_file))
        if not os.path.exists(qc_dir):
            os.makedirs(qc_dir)

        #no front trim if sequence is barcoded
        if self.options.barcode:
            self.options.trim_front = 0

        reporter = QCReporter()

        r1qc_prefilter = QualityControl(self.options.qc_sample, self.options.qc_kmer)
        r2qc_prefilter = QualityControl(self.options.qc_sample, self.options.qc_kmer)
        r1qc_prefilter.statFile(self.options.read1_file)
        r1qc_prefilter.plot(qc_dir, "R1-prefilter")
        if self.options.read2_file != None:
            r2qc_prefilter.statFile(self.options.read2_file)
            r2qc_prefilter.plot(qc_dir, "R2-prefilter")

        r1qc_postfilter = QualityControl(self.options.qc_sample, self.options.qc_kmer)
        r2qc_postfilter = QualityControl(self.options.qc_sample, self.options.qc_kmer)

        readLen = r1qc_prefilter.readLen
        overlap_histgram = [0 for x in xrange(readLen+1)]
        distance_histgram = [0 for x in xrange(readLen+1)]

        #auto detect trim front and trim tail
        if self.options.trim_front == -1 or self.options.trim_tail == -1:
            #auto trim for read1
            trimFront, trimTail = r1qc_prefilter.autoTrim()
            if self.options.trim_front == -1:
                self.options.trim_front = trimFront
            if self.options.trim_tail == -1:
                self.options.trim_tail = trimTail
            #auto trim for read2
            if self.options.read2_file != None:
                # check if we should keep same trimming for read1/read2 to keep their length identical
                # this option is on by default because lots of dedup algorithms require this feature
                if self.options.trim_pair_same:
                    self.options.trim_front2 = self.options.trim_front
                    self.options.trim_tail2 = self.options.trim_tail
                else:
                    trimFront2, trimTail2 = r2qc_prefilter.autoTrim()
                    if self.options.trim_front2 == -1:
                        self.options.trim_front2 = trimFront2
                    if self.options.trim_tail2 == -1:
                        self.options.trim_tail2 = trimTail2
                
        print(self.options.read1_file + " options:")
        print(self.options)
        
        #if good output folder not specified, set it as the same folder of read1 file
        good_dir = self.options.good_output_folder
        if good_dir == None:
            good_dir = os.path.dirname(self.options.read1_file)

        #if bad output folder not specified, set it as the same folder of read1 file            
        bad_dir = self.options.bad_output_folder
        if bad_dir == None:
            bad_dir = os.path.dirname(self.options.read1_file)

        #if overlap output folder not specified, set it as the same folder of read1 file
        overlap_dir = self.options.overlap_output_folder
        if overlap_dir == None:
            overlap_dir = os.path.dirname(self.options.read1_file)
            
        if not os.path.exists(good_dir):
            os.makedirs(good_dir)
            
        if not os.path.exists(bad_dir):
            os.makedirs(bad_dir)

        if self.options.store_overlap and self.options.read2_file != None and (not os.path.exists(overlap_dir)):
            os.makedirs(overlap_dir)
        
        good_read1_file = None
        bad_read1_file = None
        overlap_read1_file = None
        if not self.options.qc_only:
            good_read1_file = fastq.Writer(os.path.join(good_dir, getMainName(self.options.read1_file)+".good.fq"))
            bad_read1_file = fastq.Writer(os.path.join(bad_dir, getMainName(self.options.read1_file)+".bad.fq"))

            overlap_read1_file = None
            if self.options.store_overlap:
                overlap_read1_file = fastq.Writer(os.path.join(overlap_dir, getMainName(self.options.read1_file)+".overlap.fq"))
        
        #other files are optional
        read2_file = None
        good_read2_file = None
        bad_read2_file = None
        overlap_read2_file = None

        index1_file = None
        good_index1_file = None
        bad_index1_file = None
        overlap_index1_file = None

        index2_file = None
        good_index2_file = None
        bad_index2_file = None
        overlap_index2_file = None
        
        #if other files are specified, then read them
        if self.options.read2_file != None:
            read2_file = fastq.Reader(self.options.read2_file)
            if not self.options.qc_only:
                good_read2_file = fastq.Writer(os.path.join(good_dir, getMainName(self.options.read2_file)+".good.fq"))
                bad_read2_file = fastq.Writer(os.path.join(bad_dir, getMainName(self.options.read2_file)+".bad.fq"))
                if self.options.store_overlap and self.options.read2_file != None:
                    overlap_read2_file = fastq.Writer(os.path.join(overlap_dir, getMainName(self.options.read2_file)+".overlap.fq"))
        if self.options.index1_file != None:
            index1_file = fastq.Reader(self.options.index1_file)
            if not self.options.qc_only:
                good_index1_file = fastq.Writer(os.path.join(good_dir, getMainName(self.options.index1_file)+".good.fq"))
                bad_index1_file = fastq.Writer(os.path.join(bad_dir, getMainName(self.options.index1_file)+".bad.fq"))
                if self.options.store_overlap and self.options.read2_file != None:
                    overlap_index1_file = fastq.Writer(os.path.join(overlap_dir, getMainName(self.options.index1_file)+".overlap.fq"))
        if self.options.index2_file != None:
            index2_file = fastq.Reader(self.options.index2_file)
            if not self.options.qc_only:
                good_index2_file = fastq.Writer(os.path.join(good_dir, getMainName(self.options.index2_file)+".good.fq"))
                bad_index2_file = fastq.Writer(os.path.join(bad_dir, getMainName(self.options.index2_file)+".bad.fq"))
                if self.options.store_overlap and self.options.read2_file != None:
                    overlap_index2_file = fastq.Writer(os.path.join(overlap_dir, getMainName(self.options.index2_file)+".overlap.fq"))
            
        r1 = None
        r2 = None
        i1 = None
        i2 = None

        # stat numbers
        TOTAL_BASES = 0
        GOOD_BASES = 0
        TOTAL_READS = 0
        GOOD_READS = 0
        BAD_READS = 0
        BADBCD1 = 0
        BADBCD2 = 0
        BADTRIM1 = 0
        BADTRIM2 = 0
        BADBBL = 0
        BADLEN = 0
        BADPOL = 0
        BADLQC = 0
        BADNCT = 0
        BADOL = 0
        BADINDEL = 0
        BADMISMATCH = 0
        BASE_CORRECTED = 0
        OVERLAPPED = 0
        OVERLAP_LEN_SUM = 0

        while True:
            r1 = read1_file.nextRead()
            if r1==None:
                break
            else:
                TOTAL_BASES += len(r1[1])
                
            if read2_file != None:
                r2 = read2_file.nextRead()
                if r2==None:
                    break
            if index1_file != None:
                i1 = index1_file.nextRead()
                if i1==None:
                    break
            if index2_file != None:
                i2 = index2_file.nextRead()
                if i2==None:
                    break
                else:
                    TOTAL_BASES += len(r2[1])

            TOTAL_READS += 1
                    
            #barcode processing
            if self.options.barcode:
                barcodeLen1 = barcodeprocesser.detectBarcode(r1[1], self.options.barcode_length, self.options.barcode_verify)
                if barcodeLen1 == 0:
                    self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADBCD1")
                    BADBCD1 += 1
                    continue
                else:
                    if r2 == None:
                        barcodeprocesser.moveBarcodeToName(r1, self.options.barcode_length, self.options.barcode_verify)
                    else:
                        barcodeLen2 = barcodeprocesser.detectBarcode(r2[1], self.options.barcode_length, self.options.barcode_verify)
                        if barcodeLen2 == 0:
                            self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADBCD2")
                            BADBCD2 += 1
                            continue
                        else:
                            barcodeprocesser.moveAndTrimPair(r1, r2, barcodeLen1, barcodeLen2, self.options.barcode_verify)
            
            #trim
            if self.options.trim_front > 0 or self.options.trim_tail > 0:
                r1 = trim(r1, self.options.trim_front, self.options.trim_tail)
                if len(r1[1]) < 5:
                    self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADTRIM1")
                    BADTRIM1 += 1
                    continue
                if r2 != None:
                    r2 = trim(r2, self.options.trim_front2, self.options.trim_tail2)
                    if len(r2[1]) < 5:
                        self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADTRIM2")
                        BADTRIM2 += 1
                        continue

            #filter debubble
            if self.options.debubble:
                if self.isInBubble(r1[0]):
                    self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADBBL")
                    BADBBL += 1
                    continue
            
            #filter sequence length
            if len(r1[1])<self.options.seq_len_req:
                self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADLEN")
                BADLEN += 1
                continue
                    
            #check polyX
            if self.options.poly_size_limit > 0:
                poly1 = hasPolyX(r1[1], self.options.poly_size_limit, self.options.allow_mismatch_in_poly)
                poly2 = None
                if r2!=None:
                    poly2 = hasPolyX(r2[1], self.options.poly_size_limit, self.options.allow_mismatch_in_poly)
                if poly1!=None or poly2!=None:
                    self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADPOL")
                    BADPOL += 1
                    continue
            
            #check low quality count
            if self.options.unqualified_base_limit > 0:
                lowQual1 = lowQualityNum(r1, self.options.qualified_quality_phred)
                lowQual2 = 0
                if r2!=None:
                    lowQual2 = lowQualityNum(r2, self.options.qualified_quality_phred)
                if lowQual1 > self.options.unqualified_base_limit or lowQual1 > self.options.unqualified_base_limit:
                    self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADLQC")
                    BADLQC += 1
                    continue
            
            #check N number
            if self.options.n_base_limit > 0:
                nNum1 = nNumber(r1)
                nNum2 = 0
                if r2!=None:
                    nNum2 = nNumber(r2)
                if nNum1 > self.options.n_base_limit or nNum2 > self.options.n_base_limit:
                    self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADNCT")
                    BADNCT += 1
                    continue

            #check overlap and do error correction
            if r2!=None:
                (offset, overlap_len, distance) = util.overlap(r1[1], r2[1])
                overlap_histgram[overlap_len] += 1
                # deal with the case insert DNA is shorter than read length and cause offset is negative
                if offset <0 and overlap_len > 30:
                    # shift the junk bases
                    r1[1] = r1[1][0:overlap_len]
                    r1[3] = r1[3][0:overlap_len]
                    r2[1] = r2[1][-offset:-offset+overlap_len]
                    r2[3] = r2[3][-offset:-offset+overlap_len]
                    # then calc overlap again
                    (offset, overlap_len, distance) = util.overlap(r1[1], r2[1])
                if overlap_len>30:
                    OVERLAPPED += 1
                    distance_histgram[distance] += 1
                    OVERLAP_LEN_SUM += overlap_len
                    corrected = 0
                    if distance > 3:
                        self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADOL")
                        BADOL += 1
                        continue
                    elif distance>0:
                        #try to fix low quality base
                        hamming = util.hammingDistance(r1[1][len(r1[1]) - overlap_len:], util.reverseComplement(r2[1][len(r2[1]) - overlap_len:]))
                        if hamming != distance:
                            self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADINDEL")
                            BADINDEL += 1
                            continue
                        #print(r1[1][len(r1[1]) - overlap_len:])
                        #print(util.reverseComplement(r2[1][len(r2[1]) - overlap_len:]))
                        #print(r1[3][len(r1[1]) - overlap_len:])
                        #print(util.reverse(r2[3][len(r2[1]) - overlap_len:]))
                        for o in xrange(overlap_len):
                            b1 = r1[1][len(r1[1]) - overlap_len + o]
                            b2 = util.complement(r2[1][-o-1])
                            q1 = r1[3][len(r1[3]) - overlap_len + o]
                            q2 = r2[3][-o-1]
                            if b1 != b2:
                                # print(TOTAL_READS, o, b1, b2, q1, q2)
                                if util.qualNum(q1) >= 27 and util.qualNum(q2) <= 16:
                                    r2[1] = util.changeString(r2[1], -o-1, util.complement(b1))
                                    r2[3] = util.changeString(r2[3], -o-1, q1)
                                    corrected += 1
                                elif util.qualNum(q2) >= 27 and util.qualNum(q1) <= 16:
                                    r1[1]= util.changeString(r1[1], len(r1[1]) - overlap_len + o, b2)
                                    r1[3] = util.changeString(r1[3], len(r1[3]) - overlap_len + o, q2)
                                    corrected += 1
                                if corrected >= distance:
                                    break
                        #print(r1[1][len(r1[1]) - overlap_len:])
                        #print(util.reverseComplement(r2[1][len(r2[1]) - overlap_len:]))
                        #print(r1[3][len(r1[1]) - overlap_len:])
                        #print(util.reverse(r2[3][len(r2[1]) - overlap_len:]))
                        if corrected == distance:
                            BASE_CORRECTED += 1
                        else:
                            self.writeReads(r1, r2, i1, i2, bad_read1_file, bad_read2_file, bad_index1_file, bad_index2_file, "BADMISMATCH")
                            BADMISMATCH += 1
                            continue
                    if distance == 0 or distance == corrected:
                        if self.options.store_overlap:
                            self.writeReads(getOverlap(r1, overlap_len), getOverlap(r2, overlap_len), i1, i2, overlap_read1_file, overlap_read2_file, overlap_index1_file, overlap_index2_file, None)

            #write to good       
            self.writeReads(r1, r2, i1, i2, good_read1_file, good_read2_file, good_index1_file, good_index2_file, None)
            GOOD_BASES += len(r1[1])
            if i2 != None:
                GOOD_BASES += len(r2[1])
            if self.options.qc_sample <=0 or TOTAL_READS < self.options.qc_sample:
                r1qc_postfilter.statRead(r1)
                if r2 != None:
                    r2qc_postfilter.statRead(r2)

            GOOD_READS += 1
            if self.options.qc_only and TOTAL_READS >= self.options.qc_sample:
                break

        r1qc_postfilter.qc()
        r1qc_postfilter.plot(qc_dir, "R1-postfilter")
        if self.options.read2_file != None:
            r2qc_postfilter.qc()
            r2qc_postfilter.plot(qc_dir, "R2-postfilter")
        
        #close all files
        if not self.options.qc_only:
            good_read1_file.flush()
            bad_read1_file.flush()
            if self.options.read2_file != None:
                good_read2_file.flush()
                bad_read2_file.flush()
            if self.options.index1_file != None:
                good_index1_file.flush()
                bad_index1_file.flush()
            if self.options.index2_file != None:
                good_index2_file.flush()
                bad_index2_file.flush()

        # print stat numbers
        BAD_READS = TOTAL_READS - GOOD_READS
        result = {}
        result['total_bases']=TOTAL_BASES
        result['good_bases']=GOOD_BASES
        result['total_reads']=TOTAL_READS
        result['good_reads']=GOOD_READS
        result['bad_reads']=BAD_READS
        result['bad_reads_with_bad_barcode']= BADBCD1 + BADBCD2
        result['bad_reads_with_reads_in_bubble']= BADBBL
        result['bad_reads_with_bad_read_length']= BADLEN + BADTRIM1 + BADTRIM2
        result['bad_reads_with_polyX']= BADPOL
        result['bad_reads_with_low_quality']=BADLQC
        result['bad_reads_with_too_many_N']= BADNCT
        result['bad_reads_with_bad_overlap']= BADOL + BADMISMATCH + BADINDEL

        # plot result bar figure
        labels = ['good reads', 'has_polyX', 'low_quality', 'too_short', 'too_many_N']
        counts = [GOOD_READS, BADPOL, BADLQC, BADLEN + BADTRIM1 + BADTRIM2, BADNCT]
        colors = ['#66BB11', '#FF33AF', '#FFD3F2', '#FFA322', '#FF8899']
        if self.options.read2_file != None:
            labels.append('bad_overlap')
            counts.append(BADOL + BADMISMATCH + BADINDEL)
            colors.append('#FF6600')
        if self.options.debubble:
            labels.append('in_bubble')
            counts.append(BADBBL)
            colors.append('#EEBB00')
        if self.options.barcode:
            labels.append('bad_barcode')
            counts.append(BADBCD1 + BADBCD2)
            colors.append('#CCDD22')

        for i in xrange(len(counts)):
            labels[i] = labels[i] + ": " + str(counts[i]) + "(" + str(100.0 * float(counts[i])/TOTAL_READS) + "%)"

        r1qc_prefilter.plotFilterStats(labels, counts, colors, TOTAL_READS, os.path.join(qc_dir, "filter-stat.png"))

        stat={}
        # stat["options"]=self.options
        stat["summary"]=result
        stat["command"]=makeDict(self.options)
        stat["kmer_content"] = {}
        stat["kmer_content"]["read1_prefilter"] = r1qc_prefilter.topKmerCount[0:10]
        stat["kmer_content"]["read1_postfilter"] = r1qc_postfilter.topKmerCount[0:10]
        if self.options.read2_file != None:
            stat["kmer_content"]["read2_prefilter"] = r2qc_prefilter.topKmerCount[0:10]
            stat["kmer_content"]["read2_postfilter"] = r2qc_postfilter.topKmerCount[0:10]
            stat["overlap"]={}
            stat["overlap"]['overlapped_pairs']=OVERLAPPED
            if OVERLAPPED > 0:
                stat["overlap"]['average_overlap_length']=float(OVERLAP_LEN_SUM/OVERLAPPED)
            else:
                stat["overlap"]['average_overlap_length']=0.0
            stat["overlap"]['bad_edit_distance']=BADOL
            stat["overlap"]['bad_mismatch_bases']=BADMISMATCH
            stat["overlap"]['bad_indel']=BADINDEL
            stat["overlap"]['reads_with_corrected_mismatch_bases']=BASE_CORRECTED
            stat["overlap"]['overlapped_area_edit_distance_histogram']=distance_histgram[0:10]
            r1qc_prefilter.plotOverlapHistgram(overlap_histgram, readLen, TOTAL_READS, os.path.join(qc_dir, "overlap.png"))

        stat_file = open(os.path.join(qc_dir, "after.json"), "w")
        stat_json = json.dumps(stat, sort_keys=True,indent=4, separators=(',', ': '))
        stat_file.write(stat_json)
        stat_file.close()

        self.addFiguresToReport(reporter)
        reporter.output(os.path.join(qc_dir, "report.html"))

    def addFiguresToReport(self, reporter):
        reporter.addFigure('Good reads and bad reads after filtering', 'filter-stat.png')
        if self.options.read2_file != None:
            reporter.addFigure('Overlap length distribution for pair end sequencing', 'overlap.png')
        if self.options.read2_file != None:
            reporter.addFigure('Read1 quality curve before filtering', 'R1-prefilter-quality.png')
            reporter.addFigure('Read1 base content distribution before filtering', 'R1-prefilter-content.png')
            reporter.addFigure('Read1 GC curve before filtering', 'R1-prefilter-gc-curve.png')
            reporter.addFigure('Read1 per base discontinuity before filtering (window size = 5)', 'R1-prefilter-discontinuity.png')
            reporter.addFigure('Read1 kmer strand bias before filtering', 'R1-prefilter-strand-bias.png')
            reporter.addFigure('Read1 quality curve after filtering', 'R1-postfilter-quality.png')
            reporter.addFigure('Read1 base content distribution after filtering', 'R1-postfilter-content.png')
            reporter.addFigure('Read1 GC curve after filtering', 'R1-postfilter-gc-curve.png')
            reporter.addFigure('Read1 per base discontinuity after filtering (window size = 5)', 'R1-postfilter-discontinuity.png')
            reporter.addFigure('Read1 kmer strand bias after filtering', 'R1-postfilter-strand-bias.png')

            reporter.addFigure('Read2 quality curve before filtering', 'R2-prefilter-quality.png')
            reporter.addFigure('Read2 base content distribution before filtering', 'R2-prefilter-content.png')
            reporter.addFigure('Read2 GC curve before filtering', 'R2-prefilter-gc-curve.png')
            reporter.addFigure('Read2 per base discontinuity before filtering (window size = 5)', 'R2-prefilter-discontinuity.png')
            reporter.addFigure('Read2 kmer strand bias before filtering', 'R2-prefilter-strand-bias.png')
            reporter.addFigure('Read2 quality curve after filtering', 'R2-postfilter-quality.png')
            reporter.addFigure('Read2 base content distribution after filtering', 'R2-postfilter-content.png')
            reporter.addFigure('Read2 GC curve after filtering', 'R2-postfilter-gc-curve.png')
            reporter.addFigure('Read2 per base discontinuity after filtering (window size = 5)', 'R2-postfilter-discontinuity.png')
            reporter.addFigure('Read2 kmer strand bias after filtering', 'R2-postfilter-strand-bias.png')
        else:
            reporter.addFigure('Quality curve before filtering', 'R1-prefilter-quality.png')
            reporter.addFigure('Base content distribution before filtering', 'R1-prefilter-content.png')
            reporter.addFigure('GC curve before filtering', 'R1-prefilter-gc-curve.png')
            reporter.addFigure('Per base discontinuity before filtering (window size = 5)', 'R1-prefilter-discontinuity.png')
            reporter.addFigure('Kmer strand bias before filtering', 'R1-prefilter-strand-bias.png')
            reporter.addFigure('Quality curve after filtering', 'R1-postfilter-quality.png')
            reporter.addFigure('Base content distribution after filtering', 'R1-postfilter-content.png')
            reporter.addFigure('GC curve after filtering', 'R1-postfilter-gc-curve.png')
            reporter.addFigure('Per base discontinuity after filtering (window size = 5)', 'R1-postfilter-discontinuity.png')
            reporter.addFigure('Kmer strand bias after filtering', 'R1-postfilter-strand-bias.png')


