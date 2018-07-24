from boto import s3 as botos3
from boto.s3.key import Key
from datetime  import datetime
from gbdx_auth import gbdx_auth
from gbdxtools import Interface
from gbdxtools import s3
from gbdxtools import workflow
import math
import os
import pyproj
import shlex
import shutil
import subprocess
import sys
import threading
import time

class FuncThread(threading.Thread):
	def __init__(self, target, *args):
		self._target = target
		self._args = args
		threading.Thread.__init__(self)
		self._return = None

	def run(self):
		self._return = self._target(*self._args)

	def join(self):
		threading.Thread.join(self)
		return self._return

def aopImageThread(catalog_id, s3_location, panPixelSize, clip):
	aopImage(catalog_id, s3_location, local_dir = None, panPixelSize = panPixelSize, clip = clip)
	
def startAopImageThread(catalog_id, s3_location, panPixelSize, clip):
	t1 = FuncThread(aopImageThread, catalog_id, s3_location, panPixelSize, clip)
	t1.start()
	return t1

def waitForWorkflow( workflow ):
	complete = False
	while not complete:
		try:
			complete = workflow.complete
			if not complete:
				time.sleep(60)
		except:
			sys.stdout.write('.')

def aopImage(catalog_id, s3_location, local_dir = None,  panPixelSize = 0.5, clip = None):
	gbdx = Interface()
	isWV1 = catalog_id.startswith( '102' )
	if ( isWV1 ):
		print("WARNING is a WV1 image and MS or Pansharpened image can't be ordered")
	isSWIR = catalog_id.startswith( '104A' )
	if ( isSWIR ):
		print("ERROR SWIR image can't be orthorectified")
		return
	order_id = order(gbdx, catalog_id)
	data = gbdx.ordering.status(order_id)[0]['location']
	gdalwarpOptions = "  -r near --config GDAL_CACHEMAX 4000 -wm 4000 -co TILED=TRUE -co COMPRESS=PACKBITS -co BIGTIFF=YES "
    aoptask3 = gbdx.Task("AOP_Strip_Processor", data=data, enable_acomp=True, enable_pansharpen=False, enable_dra=False, ortho_epsg='UTM', bands='MS', ortho_pixel_size=str(4*panPixelSize), ortho_dem_specifier = dem)
	if ( clip is not None ):
		clipTask3 = gbdx.Task("gdalcrop", image=aoptask3.outputs.data, crop=clip+' -tr '+str(4*panPixelSize)+' '+str(4*panPixelSize)+gdalwarpOptions, ship = 'false')
		workflow = gbdx.Workflow([ aoptask3, clipTask3 ])
		workflow.savedata(clipTask3.outputs.cropped, location=s3_location+'MS')
	else:
		workflow = gbdx.Workflow([ aoptask3 ])
		workflow.savedata(aoptask3.outputs.data, location=s3_location+'MS')
	workflow.execute()
	print('AOP is processing image '+catalog_id+' MS workflow id is '+workflow.id)
	waitForWorkflow( workflow ):
	print('MS      image '+catalog_id+' '+ str(workflow.status) + ' at '+str(datetime.now()))
	if local_dir == '':
		return
	if ( local_dir is not None ):
		print('Downloading AOP images')
		if not os.path.exists(local_dir):
			os.makedirs(local_dir)
		gbdx.s3.download(location=s3_location, local_dir=local_dir)
		print('Image downloaded'+catalog_id + ' at '+str(datetime.now()))

	return

def changePrepThread(catalog_id, s3_location, clip):
	ret = changePrep(catalog_id, s3_location, clip)
	return ret
	
def startChangePrepThread(catalog_id, s3_location, clip):
	t1 = FuncThread(changePrepThread, catalog_id, s3_location, clip)
	t1.start()
	return t1

def runMTCD( s3_locationPre, s3_locationPost, out_s3_location ):
	gbdx = Interface()
	preimage = getS3location(gbdx, s3_locationPre)
	postimage = getS3location(gbdx, s3_locationPost)
	tsk = gbdx.Task('mtcd', preimage=preimage, postimage=postimage)
	wfl = gbdx.Workflow([tsk])
	wfl.savedata(tsk.outputs.data, location=out_s3_location)
	wfl.execute()
	print('MTCD image pair start '+s3_locationPre+' '+s3_locationPost+' '+out_s3_location+' '+str(wfl.id) + ' at '+str(datetime.now()))
	complete = False
	while not complete:
		try:
			complete = wfl.complete
			if not complete:
				time.sleep(60)
		except:
			sys.stdout.write('.')
	print('MTCD image pair done '+s3_locationPre+' '+s3_locationPost+' '+out_s3_location+' '+str(wfl.status) + ' at '+str(datetime.now()))
	if ( not wfl.status["event"] == "succeeded" ):
		return 1
	return 0

def MTCDThread( s3_locationPre, s3_locationPost, out_s3_location ):
	ret = runMTCD(s3_locationPre, s3_locationPost, out_s3_location);
	return ret

def startMTCDThread( s3_locationPre, s3_locationPost, out_s3_location ):
	t1 = FuncThread(MTCDThread, s3_locationPre, s3_locationPost, out_s3_location)
	t1.start()
	return t1

def runMTCDmosaic( id, s3_location ):
	gbdx = Interface()
	images = getS3location(gbdx, s3_location + "/" + str(id))
	tsk = gbdx.Task('mtcdvrt', images1=images, id=str(id))
	wfl = gbdx.Workflow([tsk])
	wfl.savedata(tsk.outputs.data, location=s3_location + "/image2image/")
	wfl.execute()
	print('MTCD mosaic start', id, images, wfl.id)
	complete = False
	while not complete:
		try:
			complete = wfl.complete
			if not complete:
				time.sleep(10)
		except:
			sys.stdout.write('.')
	print('MTCD mosaic done ' + images + ' ' + str(id) + ' ' + str(wfl.status) + ' at ' + str(datetime.now()))
	if (not wfl.status["event"] == "succeeded"):
		print('MTCD mosaic failed')
		return 1
	return 0

#mosaics all pre change images
def runMTCDmosaicPre( id, s3_location ):
	gbdx = Interface()
	if ( id == -2 ):
		# do the mosaicing of 1 and 2
		images1 = getS3location(gbdx, s3_location + "/1")
		images2 = getS3location(gbdx, s3_location + "/2")
		tsk = gbdx.Task('mtcdvrt', images1=images1, images2=images2, id="pre")
	elif ( id == -3 ):
		# do the mosaicing of 1, 2 snd 3
		images1 = getS3location(gbdx, s3_location + "/1")
		images2 = getS3location(gbdx, s3_location + "/2")
		images3 = getS3location(gbdx, s3_location + "/3")
		tsk = gbdx.Task('mtcdvrt', images1=images1, images2=images2, images3=images3, id="pre")
	else:
		print("Wrong id in runMTCDmosaicPre", id)
		return 1
	wfl = gbdx.Workflow([tsk])
	wfl.savedata(tsk.outputs.data, location=s3_location + "/image2image/")
	wfl.execute()
	print('MTCD mosaic start', id, images1, wfl.id)
	complete = False
	while not complete:
		try:
			complete = wfl.complete
			if not complete:
				time.sleep(10)
		except:
			sys.stdout.write('.')
	print('MTCD mosaic done ' + images1 + ' ' + images2 + ' ' + str(id) + ' ' + str(wfl.status) + ' at ' + str(datetime.now()))
	if (not wfl.status["event"] == "succeeded"):
		print('MTCD mosaic failed')
		return 1
	return 0

def MTCDmosaicThread( id, s3_location ):
	if ( id <  0 ):
		ret = runMTCDmosaicPre(id, s3_location);
	else:
		ret = runMTCDmosaic(id, s3_location);
	return ret

def startMTCDmosaicThread( id, s3_location ):
	t1 = FuncThread(MTCDmosaicThread, id, s3_location)
	t1.start()
	return t1

def changePrep( catalog_id, s3_location, clip = None ):
	size = '2'
	gbdx = Interface()
	isWV1 = catalog_id.startswith( '102' )
	if ( isWV1 and ( ms or pansharpen or pansharpenship )  ):
		print("ERROR Image is a WV1 image.")
		return
	isSWIR = catalog_id.startswith( '104A' )
	if ( isSWIR ):
		print("ERROR Image is a WV1 image.")
		return
	order_id = order(gbdx, catalog_id)
	data = gbdx.ordering.status(order_id)[0]['location']
	gdalwarpOptions = "  -r near --config GDAL_CACHEMAX 4000 -wm 4000 -co TILED=TRUE -co COMPRESS=PACKBITS -co BIGTIFF=YES "
	gdalwarpOptions = "  -r near -co TILED=TRUE -co COMPRESS=PACKBITS -co BIGTIFF=YES "
	aoptask = gbdx.Task("AOP_Strip_Processor", data=data, enable_acomp=True, enable_pansharpen=False, enable_dra=False, ortho_epsg='UTM', bands='MS', ortho_pixel_size=size)
	#aoptask = gbdx.Task("AOP_Strip_Processor", data=data, enable_acomp=False, enable_pansharpen=False, enable_dra=False, ortho_epsg='UTM', bands='MS', ortho_pixel_size='16')
	topoTask = gbdx.Task("topo-correction", image = aoptask.outputs.data)
	topoTask.impersonation_allowed = True
	cloudTask = gbdx.Task("CloudPuncher", image = topoTask.outputs.data, maskOnly = 'false')
	#cloudTask = gbdx.Task("CloudPuncher", image = aoptask.outputs.data, maskOnly = 'false')
	if ( clip is not None ):
		#####################################################################################################################################################################################
		clipTask = gbdx.Task('gdalcrop', image=cloudTask.outputs.mask, crop=clip+' -tr '+size+' '+size+gdalwarpOptions, ship = 'false')
		workflow = gbdx.Workflow([ aoptask, topoTask, cloudTask, clipTask ])
		workflow.savedata(clipTask.outputs.cropped, location=s3_location)
	else:
		workflow = gbdx.Workflow([ aoptask, topoTask, cloudTask ])
		workflow.savedata(cloudTask.outputs.mask, location=s3_location)
	workflow.execute()
	print('AOP is processing image '+catalog_id+' MS workflow id is '+workflow.id)
	complete = False
	while not complete:
		try:
			complete = workflow.complete
			if not complete:
				time.sleep(60)
		except:
			sys.stdout.write('.')
	print('MS      image '+catalog_id+' '+ str(workflow.status) + ' wfl id '+ workflow.id + ' at '+str(datetime.now()))
	if ( not workflow.status["event"] == "succeeded" ):
		print("workflow.status", workflow.status, workflow.status["event"] == "succeeded")
		return 1
	return 0

def processChangeImages( catalog_ids_file, s3_location, clip = None, deleteIntermediateFromS3 = False ):
	ids = []
	idsets = []
	idset = []
	f = open(catalog_ids_file, 'r')
	for line in f:
		catid = line.strip()
		if ( len(catid) > 2 ):
			ids.append(catid)
			idset.append(catid)
		else:
			if ( len(idset) ):
				idsets.append(idset)
				idset = []
	if (len(idset)):
		idsets.append(idset)
	f.close()

	processChangeSet(idsets, s3_location, clip = clip, deleteIntermediateFromS3 = deleteIntermediateFromS3)

def processChangeSet( idsets, s3_location, clip = None, deleteIntermediateFromS3 = False ):
	start_time = time.time()
	ids = []
	for idset in idsets:
		for catid in idset:
			ids.append(catid)

	print("AOP, Topocorrection, Cloud punching")
	threads = []
	id = 0
	for idset in idsets:
		id += 1
		for catID in idset:
			if ( catID is not None and len(catID) > 3 ):
				if catID.find(' ') > 0:
					catID = catID[0:catID.index(' ')]
				thread = startChangePrepThread( catID, s3_location+"/"+str(id), clip )
				threads.append(thread)
				time.sleep(2)
	nThreads = len(threads)
	i = 0
	for thread in threads:
		ret = thread.join()
		i += 1
		print(str(i)+" out of "+str(nThreads)+" threads done")
		if (ret != 0):
			print("ChangePrep error", ret)
			return 1
	print('AOP/Topo/CloudDet done. Elapsed time: {} min'.format(round((time.time() - start_time)/60)))

	print("mosaicking")
	id = 0
	threads = []
	if ( len (idsets) == 4 ):
		thread = startMTCDmosaicThread( -2, s3_location )
	else:
		thread = startMTCDmosaicThread( -3, s3_location )
	threads.append(thread)
	time.sleep(2)

	for idset in idsets:
		id += 1
		thread = startMTCDmosaicThread( id, s3_location )
		threads.append(thread)
		time.sleep(2)
	nThreads = len(threads)
	i = 0
	for thread in threads:
		ret = thread.join()
		i += 1
		print(str(i) + " out of " + str(nThreads) + " threads done. Return ", ret)
		if (ret != 0):
			print("MTCDmosaic error", ret)
			return 1
	print('mtcdvrt done. Elapsed time: {} min'.format(round((time.time() - start_time)/60)))

	threads = []
	id = 0
	for idset in idsets:
		id += 1
		if (deleteIntermediateFromS3):
			deleteFromS3(s3_location+"/"+str(id)+"/")
		fileName = str(id) +"_warped.tif"
		thread = startimage2imageThread(s3_location+"/image2image/pre.tif", s3_location+"/image2image/"+str(id)+".tif", s3_location+"/image2imageFinal/"+str(id), clip = clip, fileName = fileName )
		threads.append(thread)
		time.sleep(2)
	print("Running image2image alignment")
	nThreads = len(threads)
	i = 0
	for thread in threads:
		ret = thread.join()
		i += 1
		print(str(i)+" out of "+str(nThreads)+" threads done. Return ",ret)
		if (ret != 0):
			print("image2image error", ret)
			return 1

	print('Image2image done. Elapsed time: {} min'.format(round((time.time() - start_time)/60)))
	gbdx = Interface()
	watertsk = gbdx.Task('kk-watermask')
	watertsk.inputs.image = getS3location(gbdx, s3_location + "/image2imageFinal/1")
	waterwfl = gbdx.Workflow([watertsk])
	waterwfl.savedata(watertsk.outputs.mask, location=s3_location + "/waterMask" )
	waterwfl.execute()
	print('Water mask task start ' + str(waterwfl.id) + ' at ' + str(datetime.now()))

	nImages = len(idsets)
	threads = []
	for i in range(1, nImages+1):
		for j in range(i+1, nImages+1):
			threads.append( startMTCDThread(s3_location + "/image2imageFinal/"+str(i)+"/"+str(i)+"_warped.tif", s3_location + "/image2imageFinal/"+str(j)+"/"+str(j)+"_warped.tif", s3_location + "/changePairs/"))

	nThreads = len(threads)
	i = 0
	for thread in threads:
		ret = thread.join()
		i += 1
		print(str(i)+" out of "+str(nThreads)+" threads done. Return ",ret)
		if (ret != 0):
			print("MTCDThread error", ret)
			return 1
	print('Image pair change done. Elapsed time: {} min'.format(round((time.time() - start_time)/60)))

	if (deleteIntermediateFromS3):
		deleteFromS3(s3_location+"/image2imageFinal/")
		deleteFromS3(s3_location+"/image2image/")
	waitForWorkflow( waterwfl )
	print('Water mask task done '+ str(waterwfl.status) + ' at ' + str(datetime.now()))
	gbdx = Interface()
	changeImages = getS3location(gbdx, s3_location + "/changePairs/")
	maskFolder = getS3location(gbdx, s3_location + "/waterMask")
	tsk = gbdx.Task( 'mtcd2', image=changeImages, mask=maskFolder )
	wfl = gbdx.Workflow([tsk])
	wfl.savedata(tsk.outputs.data, location=s3_location + "/change/")
	wfl.execute()
	print('MTCD time filter start ' + str(wfl.id) + ' at ' + str(datetime.now()))
	waitForWorkflow( wfl )
	print('MTCD time filter done '+ str(wfl.status) + ' at ' + str(datetime.now()))

	if (deleteIntermediateFromS3):
		deleteFromS3(s3_location+"/changePairs/")
		deleteFromS3(s3_location + "/waterMask/")
	print('MTCD change ALL processes done. Elapsed time: {} min'.format(round((time.time() - start_time)/60)))
	print("All done " + wfl.status['event'])
	return 0

def order(gbdx, catalog_id):
	order_id = gbdx.ordering.order(catalog_id)
	if ( order_id == None ):
		print('Can not order '+ catalog_id)
		return none
	print('Ordering ' + catalog_id + ' order_id ' + order_id + ' at '+str(datetime.now()))
	status = ''
	while status != 'delivered':
		try:
			status = gbdx.ordering.status(order_id)[0]['state']
			if status != 'delivered':
				time.sleep(60)
		except:
			sys.stdout.write('.')
	prin('1B image delivered ' + gbdx.ordering.status(order_id)[0]['location'] + ' at '+str(datetime.now()))
	return order_id

def image2image(ref_image_s3_dir, image_s3_dir, output_s3, source_filename=None, reference_filename=None, clip=None, pixelSize=2, fileName = None ):
	gbdx = Interface()
	full_ref_image_s3_dir = getS3location(gbdx, ref_image_s3_dir)
	full_image_s3_dir = getS3location(gbdx, image_s3_dir)
	task = gbdx.Task("image2image", reference_directory=full_ref_image_s3_dir, source_directory=full_image_s3_dir,
					 source_filename=source_filename, reference_filename=reference_filename)
	task.timeout = 36000
	if (clip is not None):
		clipTask1 = gbdx.Task("gdalcrop", image=task.outputs.out,
							  crop=clip + ' -tr ' + str(pixelSize) + ' ' + str(pixelSize),
							  ship='False', updateName='False', fileName = fileName)
		workflow = gbdx.Workflow([task, clipTask1])
		workflow.savedata(clipTask1.outputs.cropped, location=output_s3)
	else:
		workflow = gbdx.Workflow([task])
		workflow.savedata(task.outputs.out, location=output_s3)

	workflow.execute()

	print('image2Image ' + ref_image_s3_dir + ' ' + image_s3_dir + ' ' + output_s3 + ' ' + fileName + ' workflow ' + str(
		workflow.id) + ' started at ' + str(datetime.now()))
	waitForWorkflow(workflow)
	print('image2Image ' + str(workflow.id) + ' ' + ref_image_s3_dir + ' ' + image_s3_dir + ' ' + output_s3 + ' ' + str(
		workflow.status) + ' at ' + str(datetime.now()))
	if (not workflow.status["event"] == "succeeded"):
		return 1
	return 0

def image2images(catalog_ids_file, ref_image_s3_dir, image_s3_dir, output_s3, ms=True, pan=False, pansharpen=False, clip=None, pixelSize=2, fileName = None):
	ids = []
	f = open(catalog_ids_file, 'r')
	for line in f:
		ids.append(line.strip())
	f.close()
	threads = []
	for id in ids:
		if (id is not None and len(id) > 1):
			if (ms):
				local_image_s3_dir = image_s3_dir + '/' + id + "MS"
				local_output_s3 = output_s3
				thread = startimage2imageThread(ref_image_s3_dir, local_image_s3_dir, local_output_s3, None, None, clip,
												pixelSize, fileName)
				threads.append(thread)
				time.sleep(2)
			if (pan):
				local_image_s3_dir = image_s3_dir + '/' + id + "PAN"
				local_output_s3 = output_s3
				thread = startimage2imageThread(ref_image_s3_dir, local_image_s3_dir, local_output_s3, None, None, clip,
												pixelSize, fileName)
				threads.append(thread)
				time.sleep(2)
			if (pansharpen):
				local_image_s3_dir = image_s3_dir + '/' + id + "PS"
				local_output_s3 = output_s3
				thread = startimage2imageThread(ref_image_s3_dir, local_image_s3_dir, local_output_s3, None, None, clip,
												pixelSize, fileName)
				threads.append(thread)
				time.sleep(2)
	nThreads = len(threads)
	i = 0
	for thread in threads:
		thread.join()
		i += 1
		print(str(i) + " out of " + str(nThreads) + " threads done")
	print("All done")

def image2imageThread(ref_image_s3_dir, image_s3_dir, output_s3, source_filename=None, reference_filename=None, clip=None, pixelSize=2, fileName = None):
	ret = image2image(ref_image_s3_dir, image_s3_dir, output_s3, source_filename=source_filename,
					  reference_filename=reference_filename, clip=clip, pixelSize=pixelSize, fileName=fileName)
	return ret

def startimage2imageThread(ref_image_s3_dir, image_s3_dir, output_s3, source_filename=None, reference_filename=None, clip=None, pixelSize=2, fileName = None):
	t1 = FuncThread(image2imageThread, ref_image_s3_dir, image_s3_dir, output_s3, source_filename, reference_filename,
					clip, pixelSize, fileName)
	t1.start()
	return t1

def printTaskOutput( gbdx, workflow ):
    url = "https://geobigdata.io/workflows/v1/workflows/%s" % workflow.id
    resp = gbdx.gbdx_connection.get(url)
    resp.raise_for_status()
    wf = resp.json()
    tasks = wf["tasks"]
    
    for task in tasks:
        turl = "https://geobigdata.io/workflows/v1/workflows/%s/tasks/%s/stdout" % (workflow.id, task["id"])
        r = gbdx.gbdx_connection.get(turl)
        r.raise_for_status()
    
        hurl = "https://geobigdata.io/workflows/v1/workflows/%s/tasks/%s/stderr" % (workflow.id, task["id"])
        h = gbdx.gbdx_connection.get(hurl)
        h.raise_for_status()
        print("-----------------------------------------------------------------------")
        print("TASK")
        print(task["name"])
        print("STDOUT")
        print(r.text)
        print("STDERR")
        print(h.text)
        print("\n")
		
def pri( workflowID ):
	printTaskOutputForID( workflowID )

def printTaskOutputForID( workflowID ):
    gbdx = Interface()
    url = "https://geobigdata.io/workflows/v1/workflows/%s" % workflowID
    print(url)
    resp = gbdx.gbdx_connection.get(url)
    resp.raise_for_status()
    wf = resp.json()
    tasks = wf["tasks"]
    
    for task in tasks:
        turl = "https://geobigdata.io/workflows/v1/workflows/%s/tasks/%s/stdout" % (workflowID, task["id"])
        r = gbdx.gbdx_connection.get(turl)
        r.raise_for_status()
    
        hurl = "https://geobigdata.io/workflows/v1/workflows/%s/tasks/%s/stderr" % (workflowID, task["id"])
        h = gbdx.gbdx_connection.get(hurl)
        h.raise_for_status()
        print("-----------------------------------------------------------------------")
        print("TASK")
        print(task["name"])
        print("STDOUT")
        print(r.text)
        print("STDERR")
        print(h.text)
        print("\n")
		
def getS3location(gbdx, location):
	#return 's3://gbd-customer-data/c8f66cb2-dac0-4883-8d93-12d68de252fe/'+ location
	bucket = getS3bucket()
	prefix = gbdx.s3.info['prefix']
	location = location.strip('/')
	key = Key(bucket, prefix + '/' + location)
	whats_in_here = bucket.list(prefix + '/' + location)
	exists = False
	for key in whats_in_here:
		exists = True
		break
	if not exists:
		print("WARNING!!!!! " + location + " does not exist")
	return 's3://'+gbdx.s3.info['bucket'] +'/'+ gbdx.s3.info['prefix'] +'/'+ location
	
def getUTMbox( xmin, ymin, xmax, ymax, zone = None):
	if xmax < xmin:
		print("wrong coordinates xmax < xmin")
	if ymax < ymin:
		print("wrong coordinates ymax < ymin")
	if ymax < -80 or ymin > 84:
		print("wrong coordinates y must be between -80 and 84")
	if ymax < -180 or ymin > 180:
		print("wrong coordinates x must be between -180 and 180")
	if ( zone == None ):
		median = (xmin + xmax) / 2;
		zone = math.floor((median + 180.0) / 6.0) + 1;
	hemisphere = " +north"
	if (ymax < 0 ):
		hemisphere = " +south"
	p = pyproj.Proj("+proj=utm +zone="+str(zone)+" +datum=WGS84"+hemisphere)
	xminUTM, yminUTM = p(xmin, ymin, inverse=False)
	xmaxUTM, ymaxUTM = p(xmax, ymax, inverse=False)
	print("Zone "+str(int(zone)) + " lon in m " + str(int(xmaxUTM-xminUTM)) + " lat in m " + str(int(ymaxUTM-yminUTM)))
	return str(xminUTM)+' '+str(yminUTM)+' '+str(xmaxUTM)+' '+str(ymaxUTM)

def getUTMwithProjection( xmin, ymin, xmax, ymax):
	return getUTMbox(xmin, ymin, xmax, ymax) + ' -t_srs \"' + getUTMZoneProjection(xmin, ymin, xmax,ymax) + '\"'

def getUTMZoneProjection( xmin, ymin, xmax, ymax):
	if xmax < xmin:
		print("wrong coordinates xmax < xmin")
	if ymax < ymin:
		print("wrong coordinates ymax < ymin")
	if ymax < -80 or ymin > 84:
		print("wrong coordinates y must be between -80 and 84")
	if ymax < -180 or ymin > 180:
		print("wrong coordinates x must be between -180 and 180")
	median = (xmin + xmax) / 2;
	zone = math.floor((median + 180.0) / 6.0) + 1;
	hemisphere = " +north"
	if (ymax < 0):
		hemisphere = " +south"
	return "+proj=utm +zone=" + str(zone) + " +datum=WGS84" + hemisphere

def getS3bucket():
	gbdx = Interface()
	bucket = gbdx.s3.info['bucket']
	access_key = gbdx.s3.info['S3_access_key']
	secret_key = gbdx.s3.info['S3_secret_key']
	session_token = gbdx.s3.info['S3_session_token']

	s3conn = botos3.connect_to_region('us-east-1', aws_access_key_id=access_key,
									  aws_secret_access_key=secret_key,
									  security_token=session_token)

	bucket = s3conn.get_bucket(bucket, validate=False,
						  headers={'x-amz-security-token': session_token})

	return bucket

def deleteFromS3(location):
	bucket = getS3bucket()
	prefix = Interface().s3.info['prefix']
	location = location.strip('/')

	whats_in_there = bucket.list(prefix + '/' + location)
	key = Key(bucket, prefix + '/' + location)
	print(key.name)
	if not key.exists():
		print("key " + location + " does not exist")
	for key in whats_in_there:
		bucket.delete_key(key)
