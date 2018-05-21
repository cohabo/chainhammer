#!/usr/bin/env python3
"""
@summary: query all blocks of the whole chain, to get historical TPS, etc

@attention: MOST of the code in here (all the multithreaded stuff) is actually obsolete as surprisingly the fastest way to read from parity RPC is ... single-threaded

@version: v12 (17/May/2018)
@since:   16/May/2018
@organization: electron.org.uk
@author:  https://github.com/drandreaskrueger
@see: https://gitlab.com/electronDLT/chainhammer for updates
"""

# some preliminary speed comparisons:
#
# 10 worker threads Queue:
# multithreaded into DB:     1000 blocks took 6.02 seconds
# multithreaded into file:   1000 blocks took 2.46 seconds
#                            execute & commit 1000 SQL statements into DB took 0.01 seconds
#
# single threaded into file: 1000 blocks took 2.09 seconds
#
# big surprise multithreaded slower than singlethreaded !!!
#
# multithreaded but 1 worker into file: 1000000 blocks took 1892.22 seconds
# manyBlocks_singlethreaded into file:  1970342 blocks took 4237.88 seconds
#
# execute & commit 4392280 SQL statements into DB took 37.91 seconds


RPCaddress = 'http://localhost:8545' # 8545 = default Ethereum RPC port
DBFILE = "allblocks.db"


################
## Dependencies:

import sys, time, os
from pprint import pprint

import sqlite3

from queue import Queue
from threading import Thread

from web3 import Web3, HTTPProvider # pip3 install web3

###############################################################################


def DB_createTable(dbfile=DBFILE):
    """
    creates a table with the needed columns  
    """
    conn = sqlite3.connect(dbfile)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS 
                 blocks(
                     blocknumber INTEGER UNIQUE,
                     timestamp INTEGER,
                     size INTEGER,
                     gasUsed INTEGER,
                     gasLimit INTEGER,
                     txcount INTEGER
                 )''')
    conn.commit()
    conn.close()


def DB_dropTable(dbfile=DBFILE):
    """
    removes the table
    """
    conn = sqlite3.connect(dbfile)
    c = conn.cursor()
    c.execute('''DROP TABLE IF EXISTS blocks''')
    conn.commit()
    conn.close()


def DB_writeRow_SQL(block):
    """
    takes in Ethereum block, creates sql INSERT statement 
    """
    valuesstring = "({number},{timestamp},{size},{gasUsed},{gasLimit},{txcount})"

    b = dict(block)
    b["txcount"] = len(block["transactions"])
    
    values = valuesstring.format(**b)
    
    return "INSERT INTO blocks VALUES " + values + ";"


def DB_writeRow(block, conn):
    """
    given an Ethereum block, INSERT into DB as row
    """
    
    SQL = DB_writeRow_SQL(block)

    c = conn.cursor()
    c.execute(SQL)
    conn.commit()


def writeRowIntoFile(block):
    """
    write sql INSERT command as row into textfile
    (because DB concurrency slowed it down much)
    """
    SQL = DB_writeRow_SQL(block)
    with open(DBFILE + ".sql", "a") as f:
        f.write(SQL + "\n")


def SQLfileIntoDB(conn, commitEvery=100000):
    """
    read sql commands text file and execute into DB
    """
    
    before = time.clock()
    
    c = conn.cursor()
    
    numRows, duplicates = 0, 0
    with open(DBFILE + ".sql", "r") as f:
        while True:
            line = f.readline()
            if not line:
                break
            try:
                c.execute(line)
            except Exception as e:
                print ("\n", type(e), e, line)
                duplicates += 1
            numRows += 1
            if numRows % commitEvery == 0:
                conn.commit()
                print (numRows, end=" "); sys.stdout.flush()
            lastline=line
    conn.commit()
    print ()
    print ("last one was: ", lastline)
    duration = time.clock() - before
    print ("\nexecute & commit %d SQL statements (where %d duplicates) into DB took %.2f seconds\n" % (numRows, duplicates, duration))



def DB_query(SQL, conn):
    """
    execute any SQL query, fetchall, return result
    """
    cur = conn.cursor()
    cur.execute(SQL)
    result = cur.fetchall()
    return result
    

def DB_readTable(conn):
    """
    prints the whole table
    """
    table = DB_query("SELECT * FROM blocks ORDER BY blocknumber", conn)
    pprint (table)
    return table


def DB_tableSize(conn):
    """
    prints number of rows
    """
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM blocks")
    count = cur.fetchone()
    print ("TABLE blocks has %d rows" % count)
    return count
    

def DB_blocknumberMinMax(conn):
    result = DB_query("SELECT MIN(blocknumber), MAX(blocknumber) FROM blocks", conn)
    print ("MIN(blocknumber), MAX(blocknumber) = %s " % (result) )
    return result
    

def start_web3connection(RPCaddress=None, 
                         IPCpath="TODO"): # how to enable IPC in parity ???
    """
    get a global web3 object
    """
    global w3
    w3 = Web3(HTTPProvider(RPCaddress, request_kwargs={'timeout': 120}))
    
    print ("web3 connection established, blockNumber =", w3.eth.blockNumber, end=", ")
    print ("node version string = ", w3.version.node)
    
    return w3


def getBlock(blockNumber):
    """
    Query web3 for block with this number
    """
    global w3
    b = w3.eth.getBlock(blockNumber)
    # pprint (dict(b))
    return b
    

def getBlock_then_store(blockNumber, conn=None, ifPrint=True):
    """
    query web3 block, immediately write into DB
    """
    b = getBlock(blockNumber)
    
    # DB_writeRow(b, conn) # no, concurrent DB writes slow it down much
    writeRowIntoFile(b)    # yes, simply dump into file
    
    if ifPrint:
        print ("*", end="")
        if blockNumber % 1000 == 0:
            print ("\n", blockNumber, end=" ") # newline
            sys.stdout.flush()  
    
    
def multithreadedQueue(blockNumberFrom, blockNumberTo, num_worker_threads=10):
    """
    query blocks from to, via a queue of a small number of multithreading workers 
    """
    
    q = Queue()
    
    def worker():
        # connection must be thread-local:
        conn = sqlite3.connect(DBFILE, timeout=15)
        # TODO: how to close DB connection again?? Infinite loop:
        while True:
            try:
                item = q.get()
            except Exception as e:
                print (type(e), e)
            getBlock_then_store(item, conn)
            q.task_done()

    for i in range(num_worker_threads):
         t = Thread(target=worker)
         t.daemon = True
         t.start()
    print ("%d worker threads created." % num_worker_threads)

    for i in range(blockNumberFrom, blockNumberTo):
        q.put (i)
    print ("%d items queued." % (blockNumberTo - blockNumberFrom) )
    
    print ("\n", blockNumberFrom, end=" ")

    q.join()
    print ("\nall items - done.")


def manyBlocks_multithreaded(blockNumberFrom=0, numBlocks=1000):
    """
    multithreaded block information downloader
    """
    DB_dropTable()
    DB_createTable()
    
    before = time.clock()
    multithreadedQueue(blockNumberFrom = blockNumberFrom, 
                       blockNumberTo = blockNumberFrom + numBlocks)
    duration = time.clock() - before
    print ("\n%d blocks took %.2f seconds\n" % (numBlocks, duration))


# interestingly, the above turned out to be fastest for num_worker_threads=1
# so instead of multi-threading, just go for super simple manyBlocks_singlethreaded loop:


def manyBlocks_singlethreaded(blockNumberFrom=1000001, numBlocks=3391848):
    """
    iterate through blocks, write SQL statements into text file
    """
    before = time.clock()
    print ("\n", blockNumberFrom, end=" ")
    for i in range(blockNumberFrom, blockNumberFrom+numBlocks):
        getBlock_then_store(i)
    duration = time.clock() - before
    print ("\n%d blocks took %.2f seconds\n" % (numBlocks, duration))  



def tests():
    """
    sequence of function calls used during development
    """
    DB_dropTable()
    DB_createTable()
        
    conn = sqlite3.connect(DBFILE)
    
    b=getBlock(blockNumber=2385641)
    DB_writeRow(b, conn)
    
    getBlock_then_store(blockNumber=2385642, conn=conn)
    print ()
    DB_readTable(conn)
        
    before = time.clock()
    numBlocks=1000
    blockNumberFrom=2386000
    blockNumberTo=blockNumberFrom + numBlocks 
    multithreadedQueue(blockNumberFrom=blockNumberFrom, blockNumberTo=blockNumberTo, 
                       num_worker_threads=1)
    duration = time.clock() - before
    print ("\n%d blocks took %.2f seconds\n" % (numBlocks, duration))

    before = time.clock()
    SQLfileIntoDB(conn)
    duration = time.clock() - before
    print ("\n%d SQL statements into DB took %.2f seconds\n" % (numBlocks, duration))
    
    DB_tableSize(conn)
    
    conn.close()    


def DB_newFromFile():
    """
    drop and create table, read textfile into DB 
    
    if you have many duplicates in allblocks.db.sql then this helps 
        sort allblocks.db.sql | uniq > allblocks_.db.sql; wc allblocks_.db.sql 
    """
    conn = sqlite3.connect(DBFILE)
    DB_dropTable()
    DB_createTable()
    SQLfileIntoDB(conn)
    DB_tableSize(conn)
    DB_blocknumberMinMax(conn)
    conn.close()


if __name__ == '__main__':
    
    global w3
    w3=start_web3connection(RPCaddress=RPCaddress) 

    # tests(); exit()
    # manyBlocks_multithreaded(); exit()
    # manyBlocks_singlethreaded(); exit()
    
    DB_newFromFile(); exit()
    
    # N.B.: perhaps manually delete the existing "allblocks.db.sql" before 
    blockNumberFrom=0
    blockNumberFrom=4429200
    manyBlocks_singlethreaded(blockNumberFrom=blockNumberFrom, # numBlocks=1)
                              numBlocks=w3.eth.blockNumber - blockNumberFrom)
                              
    DB_newFromFile()
    print ("done.")
    