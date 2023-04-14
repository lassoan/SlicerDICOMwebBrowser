from __future__ import division

import json
import logging
import os
import os.path
import pydicom
import shutil
import string
import sys
import time
import unittest

from __main__ import vtk, qt, ctk, slicer

from slicer.ScriptedLoadableModule import *

#
# DICOMwebBrowser
#

class DICOMwebBrowser(ScriptedLoadableModule):
  def __init__(self, parent):
    ScriptedLoadableModule.__init__(self, parent)
    self.parent.title = "DICOMweb Browser"
    self.parent.categories = ["Informatics"]
    self.parent.dependencies = ["DICOM"]
    self.parent.contributors = ["Andras Lasso (PerkLab, Queen's)",
      "Alireza Mehrtash (Brigham and Women's Hospital)",
      "Andrey Fedorov (Brigham and Women's Hospital)"]
    self.parent.helpText = """Browse and retrieve DICOM data sets from DICOMweb server."""
    self.parent.acknowledgementText = """<img src=':Logos/QIICR.png'><br><br>
    Supported by NIH U24 CA180918 (PIs Kikinis and Fedorov).
    """

#
# qDICOMwebBrowserWidget
#

class DICOMwebBrowserWidget(ScriptedLoadableModuleWidget):
  def __init__(self, parent=None):
    ScriptedLoadableModuleWidget.__init__(self, parent)

    self.showBrowserOnEnter = True
    self.DICOMwebClient = None

    self.browserWidget = qt.QWidget()
    self.browserWidget.setWindowTitle('DICOMweb Browser')

    self.seriesTableRowCount = 0
    self.selectedSeriesNicknamesDic = {}
    self.downloadQueue = []
    self.cancelDownloadRequested = False

    self.imagesToDownloadCount = 0

    databaseDirectory = slicer.dicomDatabase.databaseDirectory
    self.storagePath = databaseDirectory + "/DICOMwebLocal/"
    if not os.path.exists(self.storagePath):
      os.makedirs(self.storagePath)

    self.cachePath = self.storagePath + "/ServerResponseCache/"

    if not os.path.exists(self.cachePath):
      os.makedirs(self.cachePath)
    self.useCacheFlag = True

    self.studyFilterUpdateTimer = qt.QTimer()
    self.studyFilterUpdateTimer.setSingleShot(True)
    self.studyFilterUpdateTimer.interval = 500
    self.studyFilterUpdateTimer.connect('timeout()', self.updateStudyFilter)

  def enter(self):
    if self.showBrowserOnEnter:
      self.showBrowser()

  def setup(self):
    ScriptedLoadableModuleWidget.setup(self)

    # Ensure that correct version of dicomweb-clien Python package is installed
    needRestart = False
    needInstall = False
    minimumDicomwebClientVersion = "0.51"
    try:
      import dicomweb_client
      from packaging import version
      if version.parse(dicomweb_client.__version__) < version.parse(minimumDicomwebClientVersion):
        if not slicer.util.confirmOkCancelDisplay(f"DICOMweb browser requires installation of dicomweb-client (version {minimumDicomwebClientVersion} or later).\nClick OK to upgrade dicomweb-client and restart the application."):
          self.showBrowserOnEnter = False
          return
        needRestart = True
        needInstall = True
    except ModuleNotFoundError:
      needInstall = True

    if needInstall:
      # pythonweb-client 0.50 was broken (https://github.com/MGHComputationalPathology/dicomweb-client/issues/41)
      progressDialog = slicer.util.createProgressDialog(labelText='Upgrading dicomweb-client. This may take a minute...', maximum=0)
      slicer.app.processEvents()
      slicer.util.pip_install(f'dicomweb-client>={minimumDicomwebClientVersion}')
      import dicomweb_client
      progressDialog.close()
    if needRestart:
      slicer.util.restart()

    # Instantiate and connect widgets ...
    downloadAndIndexIcon = qt.QIcon(self.resourcePath('Icons/downloadAndIndex.png'))
    downloadAndLoadIcon = qt.QIcon(self.resourcePath('Icons/downloadAndLoad.png'))
    browserIcon = qt.QIcon(self.resourcePath('Icons/DICOMwebBrowser.png'))
    cancelIcon = qt.QIcon(self.resourcePath('Icons/cancel.png'))
    self.downloadIcon = qt.QIcon(self.resourcePath('Icons/download.png'))
    self.storedlIcon = qt.QIcon(self.resourcePath('Icons/stored.png'))
    self.browserWidget.setWindowIcon(browserIcon)

    #
    # Browser Area
    #
    browserCollapsibleButton = ctk.ctkCollapsibleButton()
    browserCollapsibleButton.text = "DICOMweb Browser"
    self.layout.addWidget(browserCollapsibleButton)
    browserLayout = qt.QVBoxLayout(browserCollapsibleButton)

    self.popupGeometry = qt.QRect()
    settings = qt.QSettings()
    mainWindow = slicer.util.mainWindow()
    if mainWindow:
      width = mainWindow.width * 0.75
      height = mainWindow.height * 0.75
      self.popupGeometry.setWidth(width)
      self.popupGeometry.setHeight(height)
      self.popupPositioned = False
      self.browserWidget.setGeometry(self.popupGeometry)

    #
    # Select DICOM Store Button
    #
    self.selectDICOMStoreButton = qt.QPushButton("Select DICOM Store")
    browserLayout.addWidget(self.selectDICOMStoreButton)

    #
    # Show Browser Button
    #
    self.showBrowserButton = qt.QPushButton("Show Browser")
    browserLayout.addWidget(self.showBrowserButton)

    # Browser Widget Layout within the collapsible button
    browserWidgetLayout = qt.QVBoxLayout(self.browserWidget)

    self.serverCollapsibleGroupBox = ctk.ctkCollapsibleGroupBox()
    self.serverCollapsibleGroupBox.setTitle('Server')
    browserWidgetLayout.addWidget(self.serverCollapsibleGroupBox)  #
    serverFormLayout = qt.QHBoxLayout(self.serverCollapsibleGroupBox)

    #
    # Collection Selector ComboBox
    #
    self.serverUrlLabel = qt.QLabel('Server URL:')
    serverFormLayout.addWidget(self.serverUrlLabel)
    # Server address selector
    self.serverUrlLineEdit = qt.QComboBox()
    self.serverUrlLineEdit.editable = True
    qSize = qt.QSizePolicy()
    qSize.setHorizontalPolicy(qt.QSizePolicy.Expanding)
    qSize.setVerticalPolicy(qt.QSizePolicy.Preferred)
    self.serverUrlLineEdit.setSizePolicy(qSize)
    self.serverUrlLineEdit.currentText = qt.QSettings().value('DICOMwebBrowser/ServerURL', '')
    serverFormLayout.addWidget(self.serverUrlLineEdit)

    self.connectToServerButton = qt.QPushButton()
    self.connectToServerButton.text = "Connect"
    serverFormLayout.addWidget(self.connectToServerButton)

    #
    # Use Cache CheckBox
    #
    self.useCacheCeckBox = qt.QCheckBox("Use cached server responses")
    self.useCacheCeckBox.toolTip = """For faster browsing, if this box is checked \
the browser will use cached server responses and on further calls would populate tables based on saved data on disk. \
Disable if data is added or removed from the database."""

    serverFormLayout.addWidget(self.useCacheCeckBox)
    self.useCacheCeckBox.setCheckState(True)
    self.useCacheCeckBox.setTristate(False)
    #logoLabelText = "<img src='" + self.resourcePath('Icons/DICOMwebBrowser.png') + "'>"
    #self.logoLabel = qt.QLabel(logoLabelText)
    #serverFormLayout.addWidget(self.logoLabel)

    #
    # Studies Table Widget
    #
    self.studiesCollapsibleGroupBox = ctk.ctkCollapsibleGroupBox()
    self.studiesCollapsibleGroupBox.setTitle('Studies')
    browserWidgetLayout.addWidget(self.studiesCollapsibleGroupBox)
    studiesVBoxLayout1 = qt.QVBoxLayout(self.studiesCollapsibleGroupBox)
    studiesExpdableArea = ctk.ctkExpandableWidget()
    studiesVBoxLayout1.addWidget(studiesExpdableArea)
    studiesVBoxLayout2 = qt.QVBoxLayout(studiesExpdableArea)
    self.studiesTableWidget = qt.QTableWidget()
    self.studiesTableWidget.setCornerButtonEnabled(True)
    self.studiesModel = qt.QStandardItemModel()
    self.studiesTableHeaderLabels = ['Study instance UID', 'Patient name', 'Patient ID', 'Modalities', 'Study date', 'Study description']
    self.studiesTableWidget.setColumnCount(len(self.studiesTableHeaderLabels))
    self.studiesTableWidget.sortingEnabled = True
    self.studiesTableWidget.hideColumn(0)
    self.studiesTableWidget.setHorizontalHeaderLabels(self.studiesTableHeaderLabels)
    studiesVBoxLayout2.addWidget(self.studiesTableWidget)
    self.studiesTreeSelectionModel = self.studiesTableWidget.selectionModel()
    self.studiesTableWidget.setSelectionBehavior(qt.QAbstractItemView.SelectRows)
    studiesVerticalheader = self.studiesTableWidget.verticalHeader()
    studiesVerticalheader.setDefaultSectionSize(20)
    self.studiesTableWidgetHeader = self.studiesTableWidget.horizontalHeader()
    self.studiesTableWidget.resizeColumnsToContents()
    self.studiesTableWidgetHeader.setStretchLastSection(True)

    studiesSelectOptionsWidget = qt.QWidget()
    studiesSelectOptionsLayout = qt.QHBoxLayout(studiesSelectOptionsWidget)
    studiesSelectOptionsLayout.setMargin(0)
    studiesVBoxLayout2.addWidget(studiesSelectOptionsWidget)
    studiesSelectLabel = qt.QLabel('Select:')
    studiesSelectOptionsLayout.addWidget(studiesSelectLabel)
    self.studiesSelectAllButton = qt.QPushButton('All')
    self.studiesSelectAllButton.enabled = False
    self.studiesSelectAllButton.setMaximumWidth(50)
    studiesSelectOptionsLayout.addWidget(self.studiesSelectAllButton)
    self.studiesSelectNoneButton = qt.QPushButton('None')
    self.studiesSelectNoneButton.enabled = False
    self.studiesSelectNoneButton.setMaximumWidth(50)
    studiesSelectOptionsLayout.addWidget(self.studiesSelectNoneButton)
    studiesFilterLabel = qt.QLabel('Filter:')
    studiesSelectOptionsLayout.addWidget(studiesFilterLabel)
    self.studiesFilter = ctk.ctkSearchBox()
    self.studiesFilter.placeholderText = "Filter..."
    self.studiesFilter.showSearchIcon = True
    studiesSelectOptionsLayout.addWidget(self.studiesFilter)
    studiesVBoxLayout1.setSpacing(0)
    studiesVBoxLayout2.setSpacing(0)
    studiesVBoxLayout1.setMargin(0)
    studiesVBoxLayout2.setContentsMargins(7, 3, 7, 7)

    #
    # Series Table Widget
    #
    self.seriesCollapsibleGroupBox = ctk.ctkCollapsibleGroupBox()
    self.seriesCollapsibleGroupBox.setTitle('Series')
    browserWidgetLayout.addWidget(self.seriesCollapsibleGroupBox)
    seriesVBoxLayout1 = qt.QVBoxLayout(self.seriesCollapsibleGroupBox)
    seriesExpdableArea = ctk.ctkExpandableWidget()
    seriesVBoxLayout1.addWidget(seriesExpdableArea)
    seriesVBoxLayout2 = qt.QVBoxLayout(seriesExpdableArea)
    self.seriesTableWidget = qt.QTableWidget()
    # self.seriesModel = qt.QStandardItemModel()
    self.seriesTableHeaderLabels = ['Series Instance UID', 'Status', 'Series number', 'Modality',
                                    'Image count', 'Series description']
    self.seriesTableWidget.setColumnCount(len(self.seriesTableHeaderLabels))
    self.seriesTableWidget.sortingEnabled = True
    self.seriesTableWidget.hideColumn(0)
    self.seriesTableWidget.setHorizontalHeaderLabels(self.seriesTableHeaderLabels)
    self.seriesTableWidget.resizeColumnsToContents()
    seriesVBoxLayout2.addWidget(self.seriesTableWidget)
    self.seriesTreeSelectionModel = self.studiesTableWidget.selectionModel()
    self.seriesTableWidget.setSelectionBehavior(qt.QAbstractItemView.SelectRows)
    self.seriesTableWidget.setSelectionMode(3)
    self.seriesTableWidgetHeader = self.seriesTableWidget.horizontalHeader()
    self.seriesItemSeriesInstanceUIDRole = qt.Qt.UserRole
    self.seriesItemStudyInstanceUIDRole = qt.Qt.UserRole+1

    self.seriesTableWidget.resizeColumnsToContents()
    self.seriesTableWidgetHeader.setStretchLastSection(True)
    seriesVerticalheader = self.seriesTableWidget.verticalHeader()
    seriesVerticalheader.setDefaultSectionSize(20)

    seriesSelectOptionsWidget = qt.QWidget()
    seriesSelectOptionsLayout = qt.QHBoxLayout(seriesSelectOptionsWidget)
    seriesVBoxLayout2.addWidget(seriesSelectOptionsWidget)
    seriesSelectOptionsLayout.setMargin(0)
    seriesSelectLabel = qt.QLabel('Select:')
    seriesSelectOptionsLayout.addWidget(seriesSelectLabel)
    self.seriesSelectAllButton = qt.QPushButton('All')
    self.seriesSelectAllButton.enabled = False
    self.seriesSelectAllButton.setMaximumWidth(50)
    seriesSelectOptionsLayout.addWidget(self.seriesSelectAllButton)
    self.seriesSelectNoneButton = qt.QPushButton('None')
    self.seriesSelectNoneButton.enabled = False
    self.seriesSelectNoneButton.setMaximumWidth(50)
    seriesSelectOptionsLayout.addWidget(self.seriesSelectNoneButton)
    seriesVBoxLayout1.setSpacing(0)
    seriesVBoxLayout2.setSpacing(0)
    seriesVBoxLayout1.setMargin(0)
    seriesVBoxLayout2.setContentsMargins(7, 3, 7, 7)

    seriesSelectOptionsLayout.addStretch(1)
    self.imagesCountLabel = qt.QLabel()
    self.imagesCountLabel.text = 'No. of images to download: <span style=" font-size:8pt; font-weight:600; color:#aa0000;">0</span>'
    seriesSelectOptionsLayout.addWidget(self.imagesCountLabel)
    # seriesSelectOptionsLayout.setAlignment(qt.Qt.AlignTop)

    # Index Button
    #
    self.indexButton = qt.QPushButton()
    self.indexButton.setMinimumWidth(50)
    self.indexButton.toolTip = "Download and Index: The browser will download the selected sereies and index them in the local DICOM Database."
    self.indexButton.setIcon(downloadAndIndexIcon)
    iconSize = qt.QSize(70, 40)
    self.indexButton.setIconSize(iconSize)
    # self.indexButton.setMinimumHeight(50)
    self.indexButton.enabled = False
    # downloadWidgetLayout.addStretch(4)
    seriesSelectOptionsLayout.addWidget(self.indexButton)

    # downloadWidgetLayout.addStretch(1)
    #
    # Load Button
    #
    self.loadButton = qt.QPushButton("")
    self.loadButton.setMinimumWidth(50)
    self.loadButton.setIcon(downloadAndLoadIcon)
    self.loadButton.setIconSize(iconSize)
    # self.loadButton.setMinimumHeight(50)
    self.loadButton.toolTip = "Download and Load: The browser will download the selected sereies and Load them into the scene."
    self.loadButton.enabled = False
    seriesSelectOptionsLayout.addWidget(self.loadButton)
    # downloadWidgetLayout.addStretch(4)

    self.cancelDownloadButton = qt.QPushButton('')
    seriesSelectOptionsLayout.addWidget(self.cancelDownloadButton)
    self.cancelDownloadButton.setIconSize(iconSize)
    self.cancelDownloadButton.toolTip = "Cancel all downloads."
    self.cancelDownloadButton.setIcon(cancelIcon)
    self.cancelDownloadButton.enabled = False

    self.statusFrame = qt.QFrame()
    browserWidgetLayout.addWidget(self.statusFrame)
    statusHBoxLayout = qt.QHBoxLayout(self.statusFrame)
    statusHBoxLayout.setMargin(0)
    statusHBoxLayout.setSpacing(0)
    self.statusLabel = qt.QLabel('')
    statusHBoxLayout.addWidget(self.statusLabel)
    statusHBoxLayout.addStretch(1)

    #
    # delete data context menu
    #
    self.seriesTableWidget.setContextMenuPolicy(2)
    self.removeSeriesFromLocalDatabaseAction = qt.QAction("Remove from disk", self.seriesTableWidget)
    self.seriesTableWidget.addAction(self.removeSeriesFromLocalDatabaseAction)
    # self.removeSeriesFromLocalDatabaseAction.enabled = False
    self.removeSeriesFromServerAction = qt.QAction("Remove from remote server", self.seriesTableWidget)
    self.seriesTableWidget.addAction(self.removeSeriesFromServerAction)

    #
    # Settings Area
    #
    settingsCollapsibleButton = ctk.ctkCollapsibleButton()
    settingsCollapsibleButton.text = "Settings"
    self.layout.addWidget(settingsCollapsibleButton)
    settingsGridLayout = qt.QGridLayout(settingsCollapsibleButton)
    settingsCollapsibleButton.collapsed = True

    # Storage Path button
    #
    # storageWidget = qt.QWidget()
    # storageFormLayout = qt.QFormLayout(storageWidget)
    # settingsVBoxLayout.addWidget(storageWidget)

    storagePathLabel = qt.QLabel("Storage Folder: ")
    self.storagePathButton = ctk.ctkDirectoryButton()
    self.storagePathButton.directory = self.storagePath
    settingsGridLayout.addWidget(storagePathLabel, 0, 0, 1, 1)
    settingsGridLayout.addWidget(self.storagePathButton, 0, 1, 1, 4)

    #
    # Connection Area
    #
    # Add remove button
    customAPILabel = qt.QLabel("Custom API Key: ")

    addRemoveApisButton = qt.QPushButton("+")
    addRemoveApisButton.toolTip = "Add or Remove APIs"
    addRemoveApisButton.enabled = True
    addRemoveApisButton.setMaximumWidth(20)

    # connections
    self.showBrowserButton.connect('clicked(bool)', self.onShowBrowserButton)
    self.selectDICOMStoreButton.connect('clicked(bool)', self.onSelectDICOMStoreButton)
    self.connectToServerButton.connect('clicked()', self.connectToServer)
    self.studiesTableWidget.connect('itemSelectionChanged()', self.studiesTableSelectionChanged)
    self.seriesTableWidget.connect('itemSelectionChanged()', self.seriesSelected)
    self.useCacheCeckBox.connect('stateChanged(int)', self.onUseCacheStateChanged)
    self.indexButton.connect('clicked(bool)', self.onIndexButton)
    self.loadButton.connect('clicked(bool)', self.onLoadButton)
    self.cancelDownloadButton.connect('clicked(bool)', self.onCancelDownloadButton)
    self.storagePathButton.connect('directoryChanged(const QString &)', self.onStoragePathButton)
    self.removeSeriesFromLocalDatabaseAction.connect('triggered()', self.onRemoveSeriesFromLocalDatabaseContextMenuTriggered)
    self.removeSeriesFromServerAction.connect('triggered()', self.onRemoveSeriesFromServerContextMenuTriggered)
    self.seriesSelectAllButton.connect('clicked(bool)', self.onSeriesSelectAllButton)
    self.seriesSelectNoneButton.connect('clicked(bool)', self.onSeriesSelectNoneButton)
    self.studiesSelectAllButton.connect('clicked(bool)', self.onStudiesSelectAllButton)
    self.studiesSelectNoneButton.connect('clicked(bool)', self.onStudiesSelectNoneButton)
    self.studiesFilter.connect('textEdited(QString)', lambda: self.studyFilterUpdateTimer.start())

    # Add vertical spacer
    self.layout.addStretch(1)

    #self.connectToServer()

  def cleanup(self):
    self.studyFilterUpdateTimer.stop()

  def onShowBrowserButton(self):
    self.showBrowser()

  def onSelectDICOMStoreButton(self):
    self.gcpSelectorDialog = GCPSelectorDialog()
    self.gcpSelectorDialog.connect("finished(int)", self.onGCPSelectorDialogFinished)

  def onGCPSelectorDialogFinished(self, result):
    # see https://cloud.google.com/healthcare-api/docs/how-tos/dicomweb
    if result == qt.QDialog.Accepted:
      url = "https://healthcare.googleapis.com/v1"
      url += f"/projects/{self.gcpSelectorDialog.project}"
      url += f"/locations/{self.gcpSelectorDialog.location}"
      url += f"/datasets/{self.gcpSelectorDialog.dataset}"
      url += f"/dicomStores/{self.gcpSelectorDialog.dicomStore}"
      url += "/dicomWeb"

      qt.QSettings().setValue('DICOMwebBrowser/ServerURL', url)
      self.serverUrlLineEdit.currentText = qt.QSettings().value('DICOMwebBrowser/ServerURL', url)
      logging.debug(f"Set server url to {url}")
    else:
      logging.debug(f"Server selection canceled")

  def onUseCacheStateChanged(self, state):
    if state == 0:
      self.useCacheFlag = False
    elif state == 2:
      self.useCacheFlag = True

  def onRemoveSeriesFromLocalDatabaseContextMenuTriggered(self):
    for uid in self.seriesInstanceUIDWidgets:
      if uid.isSelected():
        seriesInstanceUID = uid.text()
        slicer.dicomDatabase.removeSeries(seriesInstanceUID)
    self.studiesTableSelectionChanged()

  def onRemoveSeriesFromServerContextMenuTriggered(self):
    studySeriesToDelete = []
    for seriesWidget in self.seriesInstanceUIDWidgets:
      if seriesWidget.isSelected():
        seriesInstanceUID = seriesWidget.text()
        studyInstanceUID = seriesWidget.data(self.seriesItemStudyInstanceUIDRole)
        studySeriesToDelete.append((studyInstanceUID, seriesInstanceUID))
    if not studySeriesToDelete:
      # nothing is selected
      return
    # Ask user confirmation
    if not slicer.util.confirmYesNoDisplay("Are you sure you want to delete {0} series from the remote server?".format(len(studySeriesToDelete)),
      parent=self.browserWidget):
      return
    # Delete from server
    for studyInstanceUID, seriesInstanceUID in studySeriesToDelete:
      self.DICOMwebClient.delete_series(studyInstanceUID, seriesInstanceUID)
    # Update view
    originalUseCacheFlag = self.useCacheFlag
    self.useCacheFlag = False
    self.studiesTableSelectionChanged()
    self.useCacheFlag = originalUseCacheFlag

  def showBrowser(self):
    if not self.browserWidget.isVisible():
      self.popupPositioned = False
      self.browserWidget.show()
      if self.popupGeometry.isValid():
        self.browserWidget.setGeometry(self.popupGeometry)
    self.browserWidget.raise_()

    if not self.popupPositioned:
      mainWindow = slicer.util.mainWindow()
      screenMainPos = mainWindow.pos
      x = screenMainPos.x() + 100
      y = screenMainPos.y() + 100
      self.browserWidget.move(qt.QPoint(x, y))
      self.popupPositioned = True

  def showStatus(self, message, waitMessage='Waiting for DICOMweb server .... '):
    self.statusLabel.text = waitMessage + message
    self.statusLabel.setStyleSheet("QLabel { background-color : #F0F0F0 ; color : #383838; }")
    slicer.app.processEvents()

  def clearStatus(self):
    self.statusLabel.text = ''
    self.statusLabel.setStyleSheet("QLabel { background-color : white; color : black; }")

  def onStoragePathButton(self):
    self.storagePath = self.storagePathButton.directory

  def onStudiesSelectAllButton(self):
    self.studiesTableWidget.selectAll()

  def onStudiesSelectNoneButton(self):
    self.studiesTableWidget.clearSelection()

  def onSeriesSelectAllButton(self):
    self.seriesTableWidget.selectAll()

  def onSeriesSelectNoneButton(self):
    self.seriesTableWidget.clearSelection()

  def addServerUrlToHistory(self, url):
    urlHistory = list(qt.QSettings().value('DICOMwebBrowser/ServerURLHistory', []))
    # move the url to the first position
    if url in urlHistory:
      urlHistory.remove(url)
    urlHistory.insert(0, url)
    # keep the 10 most recent urls
    urlHistory = urlHistory[:10]
    qt.QSettings().setValue('DICOMwebBrowser/ServerURLHistory', urlHistory)
    self.serverUrlLineEdit.clear()
    self.serverUrlLineEdit.addItems(urlHistory)

  def connectToServer(self):
    # Save current server URL to application settings
    qt.QSettings().setValue('DICOMwebBrowser/ServerURL', self.serverUrlLineEdit.currentText)
    self.addServerUrlToHistory(self.serverUrlLineEdit.currentText)

    self.loadButton.enabled = False
    self.indexButton.enabled = False
    self.clearStudiesTableWidget()
    self.clearSeriesTableWidget()
    self.serverUrl = self.serverUrlLineEdit.currentText
    import hashlib
    cacheFile = self.cachePath + hashlib.md5(self.serverUrl.encode()).hexdigest() + '.json'
    self.progressMessage = "Getting available studies for server: " + self.serverUrl
    self.showStatus(self.progressMessage)

    import dicomweb_client.log
    dicomweb_client.log.configure_logging(2)
    from dicomweb_client.api import DICOMwebClient
    effectiveServerUrl = self.serverUrl
    session = None
    headers = {}
    # Setting up of the DICOMweb client from various server parameters can be done
    # in plugins in the future, but for now just hardcode special initialization
    # steps for a few server types.
    if "googleapis.com" in self.serverUrl:
      # Google Healthcare API
      headers["Authorization"] = f"Bearer {GoogleCloudPlatform().token()}"
    elif "kheops" in self.serverUrl:
      # Kheops DICOMweb API endpoint from browser view URL
      url = qt.QUrl(self.serverUrl)
      if url.path().startswith('/view/'):
        # This is a Kheops viewer URL.
        # Retrieve the token from the viewer URL and use the Kheops API URL to connect to the server.
        token = url.path().replace('/view/','')
        effectiveServerUrl = f"{url.scheme()}://{url.host()}/api"
        from requests.auth import HTTPBasicAuth
        from dicomweb_client.session_utils import create_session_from_auth
        auth = HTTPBasicAuth('token', token)
        session = create_session_from_auth(auth)

    self.DICOMwebClient = DICOMwebClient(url=effectiveServerUrl, session=session, headers=headers)

    studiesList = None
    if os.path.isfile(cacheFile) and self.useCacheFlag:
      with open(cacheFile, 'r') as openfile:
        studiesList = json.load(openfile)
      if not len(studiesList):
        studiesList = None

    if studiesList:
      self.populateStudiesTableWidget(studiesList)
      self.clearStatus()
      groupBoxTitle = 'Studies (Accessed: ' + time.ctime(os.path.getmtime(cacheFile)) + ')'
      self.studiesCollapsibleGroupBox.setTitle(groupBoxTitle)

    else:
      try:
        # Get all studies
        studies = []
        offset = 0

        while True:
            subset = self.DICOMwebClient.search_for_studies(offset=offset, fields=['StudyDescription'])
            if len(subset) == 0:
                break
            if subset[0] in studies:
                # got the same study twice, so probably this server does not respect offset,
                # therefore we cannot do paging
                break
            studies.extend(subset)
            offset += len(subset)

        # Save to cache
        with open(cacheFile, 'w') as f:
          json.dump(studies, f)

        self.populateStudiesTableWidget(studies)
        groupBoxTitle = 'Studies (Accessed: ' + time.ctime(os.path.getmtime(cacheFile)) + ')'
        self.studiesCollapsibleGroupBox.setTitle(groupBoxTitle)
        self.clearStatus()

      except Exception as error:
        self.clearStatus()
        message = "connectToServer: Error in getting response from DICOMweb server.\nError:\n" + str(error)
        qt.QMessageBox.critical(slicer.util.mainWindow(),
                    'DICOMweb Browser', message, qt.QMessageBox.Ok)

    self.clearSeriesTableWidget()
    self.numberOfSelectedStudies = 0
    for widgetIndex in range(len(self.studyInstanceUIDWidgets)):
      if self.studyInstanceUIDWidgets[widgetIndex].isSelected():
        self.numberOfSelectedStudies += 1
        self.studySelected(widgetIndex)

  def studySelected(self, row):
    self.loadButton.enabled = False
    self.indexButton.enabled = False
    self.selectedStudyInstanceUID = self.studyInstanceUIDWidgets[row].text()
    self.selectedStudyRow = row
    cacheFile = self.cachePath + self.selectedStudyInstanceUID + '.json'
    self.progressMessage = "Getting available series for studyInstanceUID: " + self.selectedStudyInstanceUID
    self.showStatus(self.progressMessage)
    if os.path.isfile(cacheFile) and self.useCacheFlag:
      with open(cacheFile, 'r') as openfile:
        series = json.load(openfile)
      self.populateSeriesTableWidget(self.selectedStudyInstanceUID, series)
      self.clearStatus()
      if self.numberOfSelectedStudies == 1:
        groupBoxTitle = 'Series (Accessed: ' + time.ctime(os.path.getmtime(cacheFile)) + ')'
      else:
        groupBoxTitle = 'Series '

      self.seriesCollapsibleGroupBox.setTitle(groupBoxTitle)

    else:
      try:
        series = self.DICOMwebClient.search_for_series(self.selectedStudyInstanceUID, fields=['SeriesNumber'])
        # Save to cache
        with open(cacheFile, 'w') as f:
          json.dump(series, f)
        self.populateSeriesTableWidget(self.selectedStudyInstanceUID, series)
        if self.numberOfSelectedStudies == 1:
          groupBoxTitle = 'Series (Accessed: ' + time.ctime(os.path.getmtime(cacheFile)) + ')'
        else:
          groupBoxTitle = 'Series '

        self.seriesCollapsibleGroupBox.setTitle(groupBoxTitle)
        self.clearStatus()

      except Exception as error:
        import traceback
        traceback.print_exc()
        self.clearStatus()
        message = "studySelected: Error in getting response from DICOMweb server.\nError:\n" + str(error)
        qt.QMessageBox.critical(slicer.util.mainWindow(),
                    'DICOMweb Browser', message, qt.QMessageBox.Ok)

    self.onSeriesSelectAllButton()
    # self.loadButton.enabled = True
    # self.indexButton.enabled = True

  def updateStudyFilter(self):
    table = self.studiesTableWidget
    tableColumns = self.studiesTableHeaderLabels
    filterText = self.studiesFilter.text.upper()
    rowCount = table.rowCount
    for rowIndex in range(rowCount):
      if filterText:
        show = False
        for columnName in tableColumns:
          cellText = table.item(rowIndex, tableColumns.index(columnName)).text().upper()
          if filterText in cellText:
            show = True
            break
      else:
        show = True
      if show:
        table.showRow(rowIndex)
      else:
        table.hideRow(rowIndex)
        # We could consider remove seelction of hidden rows.

    self.studiesTableWidget.resizeColumnsToContents()
    self.studiesTableWidgetHeader.setStretchLastSection(True)

  def studiesTableSelectionChanged(self):
    self.clearSeriesTableWidget()
    self.seriesTableRowCount = 0
    self.numberOfSelectedStudies = 0
    for widgetIndex in range(len(self.studyInstanceUIDWidgets)):
      if self.studyInstanceUIDWidgets[widgetIndex].isSelected():
        self.numberOfSelectedStudies += 1
        self.studySelected(widgetIndex)

  def seriesSelected(self):
    self.imagesToDownloadCount = 0
    self.loadButton.enabled = False
    self.indexButton.enabled = False
    for widgetIndex in range(len(self.seriesInstanceUIDWidgets)):
      if self.seriesInstanceUIDWidgets[widgetIndex].isSelected():
        #self.imagesToDownloadCount += int(self.imageCounts[widgetIndex].text())
        self.imagesToDownloadCount += 1 # TODO: check if image count can be quickly retrieved
        self.loadButton.enabled = True
        self.indexButton.enabled = True
    self.imagesCountLabel.text = 'No. of images to download: ' + '<span style=" font-size:8pt; font-weight:600; color:#aa0000;">' + str(
      self.imagesToDownloadCount) + '</span>' + ' '

  def onIndexButton(self):
    self.addSelectedToDownloadQueue(loadToScene=False)

  def onLoadButton(self):
    self.addSelectedToDownloadQueue(loadToScene=True)

  def onCancelDownloadButton(self):
    self.cancelDownloadRequested = True

  def addSelectedToDownloadQueue(self, loadToScene):
    try:
      qt.QApplication.setOverrideCursor(qt.Qt.WaitCursor)

      self.cancelDownloadRequested = False
      allSelectedSeriesUIDs = []

      import hashlib
      for widgetIndex in range(len(self.seriesInstanceUIDWidgets)):
        # print self.seriesInstanceUIDWidgets[widgetIndex]
        if not self.seriesInstanceUIDWidgets[widgetIndex].isSelected():
          continue
        selectedCollection = self.serverUrl
        selectedPatient = ""  # TODO
        selectedStudy = self.selectedStudyInstanceUID
        selectedSeriesInstanceUID = self.seriesInstanceUIDWidgets[widgetIndex].text()
        allSelectedSeriesUIDs.append(selectedSeriesInstanceUID)
        self.selectedSeriesNicknamesDic[selectedSeriesInstanceUID] = "{0} - {1} - {2}".format(selectedPatient, self.selectedStudyRow + 1, widgetIndex + 1)

        # create download queue
        downloadProgressBar = qt.QProgressBar()
        self.seriesTableWidget.setCellWidget(widgetIndex, self.seriesTableHeaderLabels.index('Status'), downloadProgressBar)
        # Download folder name is set from series instance
        downloadFolderPath = os.path.join(self.storagePath, hashlib.md5(selectedSeriesInstanceUID.encode()).hexdigest()) + os.sep
        self.downloadQueue.append({'studyInstanceUID': selectedStudy, 'seriesInstanceUID': selectedSeriesInstanceUID,
                                    'downloadFolderPath': downloadFolderPath, 'downloadProgressBar': downloadProgressBar})

      self.seriesTableWidget.clearSelection()
      self.studiesTableWidget.enabled = False
      self.serverUrlLineEdit.enabled = False

      # Download data sets
      selectedSeriesUIDs = self.downloadSelectedSeries()

      # Load data sets into the scene
      if loadToScene:
        for seriesIndex, seriesUID in enumerate(allSelectedSeriesUIDs):
          if self.cancelDownloadRequested:
            break
          # Print progress message
          self.progressMessage = "loading series {0}/{1}".format(
            seriesIndex+1, len(allSelectedSeriesUIDs))  # TODO: show some more info, such as self.selectedSeriesNicknamesDic[seriesUID]
          self.showStatus(self.progressMessage, waitMessage='Loading data into the scene .... ')
          logging.debug(self.progressMessage)
          # Load data
          from DICOMLib import DICOMUtils
          DICOMUtils.loadSeriesByUID([seriesUID])

      qt.QApplication.restoreOverrideCursor()
    except Exception as error:
      qt.QApplication.restoreOverrideCursor()
      slicer.util.errorDisplay("Data loading failed.", parent=self.browserWidget, detailedText=traceback.format_exc())

    self.clearStatus()

  def getSeriesRowNumber(self, seriesInstanceUID):
    table = self.seriesTableWidget
    seriesInstanceUIDColumnIndex = self.seriesTableHeaderLabels.index('Series Instance UID')
    for rowIndex in range(table.rowCount):
      if table.item(rowIndex, seriesInstanceUIDColumnIndex).text() == seriesInstanceUID:
        return rowIndex
    # not found
    return -1

  def getSeriesDownloadProgressBar(self, seriesInstanceUID):
    rowIndex = self.getSeriesRowNumber(seriesInstanceUID)
    if rowIndex < 0:
      return None
    return self.seriesTableWidget.cellWidget(rowIndex, self.seriesTableHeaderLabels.index('Status'))

  def downloadSelectedSeries(self):
    import os
    self.cancelDownloadButton.enabled = True
    indexer = ctk.ctkDICOMIndexer()
    while self.downloadQueue and not self.cancelDownloadRequested:
      queuedItem = self.downloadQueue.pop(0)
      selectedStudy = queuedItem['studyInstanceUID']
      selectedSeries = queuedItem['seriesInstanceUID']
      downloadFolderPath = queuedItem['downloadFolderPath']
      if not os.path.exists(downloadFolderPath):
        logging.debug("Creating directory to keep the downloads: " + downloadFolderPath)
        os.makedirs(downloadFolderPath)
      self.progressMessage = "Downloading Images for series InstanceUID: " + selectedSeries
      self.showStatus(self.progressMessage)
      logging.debug(self.progressMessage)
      try:
        #response = self.DICOMwebClient.get_image(seriesInstanceUid=selectedSeries)
        instances = self.DICOMwebClient.search_for_instances(
          study_instance_uid=selectedStudy,
          series_instance_uid=selectedSeries
          )
        self.progressMessage = "Retrieving data from server"
        logging.debug("Retrieving data from server")
        self.showStatus(self.progressMessage)
        slicer.app.processEvents()
        import hashlib
        import pydicom
        # Save server response in current directory
        numberOfInstances = len(instances)

        currentDownloadProgressBar = self.getSeriesDownloadProgressBar(selectedSeries)
        if currentDownloadProgressBar:
          currentDownloadProgressBar.setMaximum(numberOfInstances)
          currentDownloadProgressBar.setValue(0)

        # Scroll to item being currently downloaded
        rowIndex = self.getSeriesRowNumber(selectedSeries)
        self.seriesTableWidget.scrollToItem(self.seriesTableWidget.item(rowIndex, self.seriesTableHeaderLabels.index('Status')))

        instancesAlreadyInDatabase = slicer.dicomDatabase.instancesForSeries(selectedSeries)

        for instanceIndex, instance in enumerate(instances):
          if self.cancelDownloadRequested:
            break
          if currentDownloadProgressBar:
            currentDownloadProgressBar.setValue(instanceIndex)
          slicer.app.processEvents()

          sopInstanceUid = instance['00080018']['Value'][0]
          if sopInstanceUid in instancesAlreadyInDatabase:
            # instance is already in database
            continue

          fileName = downloadFolderPath + hashlib.md5(sopInstanceUid.encode()).hexdigest() + '.dcm'
          if not os.path.isfile(fileName) or not self.useCacheFlag:
            # logging.debug("Downloading file {0} ({1}) from the DICOMweb server".format(
            #   filename, sopInstanceUid)
            retrievedInstance = self.DICOMwebClient.retrieve_instance(
              study_instance_uid=selectedStudy,
              series_instance_uid=selectedSeries,
              sop_instance_uid=sopInstanceUid)
            pydicom.filewriter.write_file(fileName, retrievedInstance)

        self.clearStatus()

        rowIndex = self.getSeriesRowNumber(selectedSeries)
        table = self.seriesTableWidget
        item = table.item(rowIndex, 1)
        item.setIcon(self.downloadIcon if self.cancelDownloadRequested else self.storedlIcon)
        if not self.cancelDownloadRequested:
          if currentDownloadProgressBar:
            currentDownloadProgressBar.setValue(numberOfInstances)
          # Import the data into dicomAppWidget and open the dicom browser
          self.progressMessage = "Adding Files to DICOM Database "
          self.showStatus(self.progressMessage)
          indexer.addDirectory(slicer.dicomDatabase, downloadFolderPath, True)  # index with file copy
          indexer.waitForImportFinished()
          # Indexing completed, remove files
          import os
          for f in os.listdir(downloadFolderPath):
            os.remove(os.path.join(downloadFolderPath, f))
          os.rmdir(downloadFolderPath)

        # logging.error("Failed to download images!")
        self.removeDownloadProgressBar(selectedSeries)

      except Exception as error:
        self.clearStatus()
        message = "downloadSelectedSeries: Error in getting response from DICOMweb server.\nHTTP Error:\n" + str(error)
        qt.QMessageBox.critical(slicer.util.mainWindow(),
                    'DICOMweb Browser', message, qt.QMessageBox.Ok)

    # Remove remaining queued items (items can remain if the download was cancelled)
    for queuedItem in self.downloadQueue:
      self.removeDownloadProgressBar(queuedItem['seriesInstanceUID'])
    self.downloadQueue = []

    self.cancelDownloadButton.enabled = False
    self.serverUrlLineEdit.enabled = True
    self.studiesTableWidget.enabled = True

  def removeDownloadProgressBar(self, selectedSeries):
    rowIndex = self.getSeriesRowNumber(selectedSeries)
    if rowIndex < 0:
      # Already removed
      return
    self.seriesTableWidget.setCellWidget(rowIndex, self.seriesTableHeaderLabels.index('Status'), None)

  def setTableCellTextFromDICOM(self, table, columnNames, dicomTags, rowIndex, columnName, columnDicomTag):
    try:
      if isinstance(dicomTags, pydicom.dataset.Dataset):
        values = dicomTags[columnDicomTag].value
        value = str(values)
      else:
        if dicomTags[columnDicomTag]['vr'] == 'PN':
          values = [value['Alphabetic'] for value in dicomTags[columnDicomTag]['Value']]
          value = ', '.join(list(values))
        else:
          values = dicomTags[columnDicomTag]['Value']
          value = ', '.join(list(values))
    except:
      # tag not found
      values = None
      value = ''
    widget = qt.QTableWidgetItem(value)
    table.setItem(rowIndex, columnNames.index(columnName), widget)
    return widget, values

  def populateStudiesTableWidget(self, studies):
    self.studiesSelectAllButton.enabled = True
    self.studiesSelectNoneButton.enabled = True
    # self.clearStudiesTableWidget()
    table = self.studiesTableWidget
    tableColumns = self.studiesTableHeaderLabels

    rowIndex = self.studiesTableRowCount
    table.setRowCount(rowIndex + len(studies))


    for study in studies:
      widget, value = self.setTableCellTextFromDICOM(table, self.studiesTableHeaderLabels, study, rowIndex, 'Study instance UID', '0020000D')
      self.studyInstanceUIDWidgets.append(widget)
      self.setTableCellTextFromDICOM(table, tableColumns, study, rowIndex, 'Patient name', '00100010')
      self.setTableCellTextFromDICOM(table, tableColumns, study, rowIndex, 'Patient ID', '00100020')
      self.setTableCellTextFromDICOM(table, tableColumns, study, rowIndex, 'Modalities', '00080061') # Modalities in Study
      self.setTableCellTextFromDICOM(table, tableColumns, study, rowIndex, 'Study date', '00080020')
      self.setTableCellTextFromDICOM(table, tableColumns, study, rowIndex, 'Study description', '00081030')
      rowIndex += 1

    # Resize columns
    self.studiesTableWidget.resizeColumnsToContents()
    self.studiesTableWidgetHeader.setStretchLastSection(True)
    self.studiesTableRowCount = rowIndex

    self.updateStudyFilter()

  def populateSeriesTableWidget(self, studyUID, series):
    # self.clearSeriesTableWidget()
    table = self.seriesTableWidget
    tableColumns = self.seriesTableHeaderLabels
    self.seriesSelectAllButton.enabled = True
    self.seriesSelectNoneButton.enabled = True

    rowIndex = self.seriesTableRowCount
    table.setRowCount(rowIndex + len(series))


    import dicomweb_client
    for serieJson in series:
      serie = pydicom.dataset.Dataset.from_json(serieJson)
      if hasattr(serie, 'SeriesInstanceUID'):
        widget, seriesInstanceUID = self.setTableCellTextFromDICOM(table, tableColumns, serie, rowIndex, 'Series Instance UID', 'SeriesInstanceUID')
        widget.setData(self.seriesItemSeriesInstanceUIDRole, serie.SeriesInstanceUID)
        widget.setData(self.seriesItemStudyInstanceUIDRole, studyUID)
        self.seriesInstanceUIDWidgets.append(widget)
        # Download status item
        if slicer.dicomDatabase.studyForSeries(seriesInstanceUID):
          self.removeSeriesFromLocalDatabaseAction.enabled = True
          icon = self.storedlIcon
        else:
          icon = self.downloadIcon
        downloadStatusItem = qt.QTableWidgetItem('')
        downloadStatusItem.setTextAlignment(qt.Qt.AlignCenter)
        downloadStatusItem.setIcon(icon)
        table.setItem(rowIndex, self.seriesTableHeaderLabels.index('Status'), downloadStatusItem)

      self.setTableCellTextFromDICOM(table, tableColumns, serie, rowIndex, 'Modality', 'Modality')
      self.setTableCellTextFromDICOM(table, tableColumns, serie, rowIndex, 'Series number', 'SeriesNumber')
      self.setTableCellTextFromDICOM(table, tableColumns, serie, rowIndex, 'Series description', 'SeriesDescription')
      self.setTableCellTextFromDICOM(table, tableColumns, serie, rowIndex, 'Image count', 'NumberOfSeriesRelatedInstances')

      rowIndex += 1

    self.seriesTableRowCount = rowIndex

    # # Resize columns
    # self.seriesTableWidget.resizeColumnsToContents()
    # self.seriesTableWidgetHeader.setStretchLastSection(True)

  def clearStudiesTableWidget(self):
    self.studiesTableRowCount = 0
    table = self.studiesTableWidget
    self.studiesCollapsibleGroupBox.setTitle('Studies')
    self.studyInstanceUIDWidgets = []
    table.clear()
    table.setHorizontalHeaderLabels(self.studiesTableHeaderLabels)

  def clearSeriesTableWidget(self):
    self.seriesTableRowCount = 0
    table = self.seriesTableWidget
    self.seriesCollapsibleGroupBox.setTitle('Series')
    self.seriesInstanceUIDWidgets = []
    table.clear()
    table.setHorizontalHeaderLabels(self.seriesTableHeaderLabels)

  def onReloadAndTest(self, moduleName="DICOMwebBrowser"):
    self.onReload()
    evalString = 'globals()["%s"].%sTest()' % (moduleName, moduleName)
    tester = eval(evalString)
    tester.runTest()

class GCPSelectorDialog(qt.QDialog):
  """Implement the Qt dialog for selecting a GCP DICOM Store
  """

  def __init__(self, parent="mainWindow"):
    super(GCPSelectorDialog, self).__init__(slicer.util.mainWindow() if parent == "mainWindow" else parent)
    self.setWindowTitle('Select DICOM Store')
    self.setWindowModality(1)
    self.setLayout(qt.QVBoxLayout())
    self.setMinimumWidth(600)
    self.gcp = GoogleCloudPlatform()
    self.open()

    self.project = None
    self.location = None
    self.dataset = None
    self.dicomStore = None

  def open(self):
    # Send Parameters
    self.dicomFrame = qt.QFrame(self)
    self.dicomFormLayout = qt.QFormLayout()
    self.dicomFrame.setLayout(self.dicomFormLayout)

    self.projectSelectorCombobox = qt.QComboBox()
    self.dicomFormLayout.addRow("Project: ", self.projectSelectorCombobox)
    self.projectSelectorCombobox.addItems(self.gcp.projects())
    self.projectSelectorCombobox.connect("currentIndexChanged(int)", self.onProjectSelected)

    self.datasetSelectorCombobox = qt.QComboBox()
    self.dicomFormLayout.addRow("Dataset: ", self.datasetSelectorCombobox)
    self.datasetSelectorCombobox.connect("currentIndexChanged(int)", self.onDatasetSelected)

    self.dicomStoreSelectorCombobox = qt.QComboBox()
    self.dicomFormLayout.addRow("DICOM Store: ", self.dicomStoreSelectorCombobox)
    self.dicomStoreSelectorCombobox.connect("currentIndexChanged(int)", self.onDICOMStoreSelected)

    self.layout().addWidget(self.dicomFrame)

    # button box
    self.bbox = qt.QDialogButtonBox(self)
    self.bbox.addButton(self.bbox.Ok)
    self.bbox.button(self.bbox.Ok).enabled = False
    self.bbox.addButton(self.bbox.Cancel)
    self.bbox.accepted.connect(self.onOk)
    self.bbox.rejected.connect(self.onCancel)
    self.layout().addWidget(self.bbox)

    qt.QDialog.open(self)


  def onProjectSelected(self):
    currentText = self.projectSelectorCombobox.currentText
    if currentText != "":
      self.project = currentText.split()[0]
      self.datasetSelectorCombobox.clear()
      self.dicomStoreSelectorCombobox.clear()
      qt.QTimer.singleShot(0, lambda : self.datasetSelectorCombobox.addItems(self.gcp.datasets(self.project)))

  def onDatasetSelected(self):
    currentText = self.datasetSelectorCombobox.currentText
    if currentText != "":
      datasetTextList = currentText.split()
      self.dataset = datasetTextList[0]
      self.location = datasetTextList[1]
      self.dicomStoreSelectorCombobox.clear()
      qt.QTimer.singleShot(0, lambda : self.dicomStoreSelectorCombobox.addItems(self.gcp.dicomStores(self.project, self.dataset)))

  def onDICOMStoreSelected(self):
    currentText = self.dicomStoreSelectorCombobox.currentText
    if currentText != "":
      self.dicomStore = currentText.split()[0]
      self.bbox.button(self.bbox.Ok).enabled = True

  def onOk(self):
    self.accept()
    self.close()

  def onCancel(self):
    self.projectSelectorCombobox.clear()
    self.datasetSelectorCombobox.clear()
    self.dicomStoreSelectorCombobox.clear()
    self.reject()
    self.close()


class GoogleCloudPlatform(object):
  gcloudPath=None

  def __init__(self):
    if not GoogleCloudPlatform.gcloudPath:
      GoogleCloudPlatform.gcloudPath = self.findgcloud()

  def findgcloud(self):
    gcloudPath = shutil.which("gcloud")
    if gcloudPath:
      return gcloudPath

    error_message = "Unable to locate gcloud, please install the Google Cloud SDK"
    if sys.platform in ("linux", "darwin"):
      # The default setup for gcloud modifies the PATH in bashrc (linux) or zshrc (macos)
      # If slicer is launched via the desktop UI in macos or linux instead of a shell environment,
      # then PATH variable is not modified to include the google cloud sdk path and shutil.which
      # will be unable to locate gcloud. Instead launch the shell in a seperate process and run 'which gcloud'
      shell = os.environ.get("SHELL","")
      if shell:
        # Search through all output lines and find one valid path to gcloud
        # An interactive shell required to run .bashrc/.zshrc
        # but an interactive shell may print out other text such as
        # startup information from oh-my-zsh or other configurations the user setup.
        process = slicer.util.launchConsoleProcess([shell, "-i", "-c", "which gcloud"])
        process.wait()
        for cmd_output in process.stdout.read().split('\n'):
          gcloudPath = cmd_output.strip()
          if gcloudPath.endswith('gcloud') and os.path.exists(gcloudPath):
            return gcloudPath

      error_message = error_message+ " and setup the PATH variable to the Google Cloud SDK bin directory in"
      shell_name = shell.split('/')[-1]
      if shell_name == "bash":
        error_message = error_message+" ~/.bashrc"
      elif shell_name == "zsh":
        error_message = error_message+" ~/.zshrc"
      else:
        error_message = error_message+f" your {shell_name} config file"

    raise RuntimeError(error_message)

  def gcloud(self, subcommand):
    args = [GoogleCloudPlatform.gcloudPath]
    args.extend(subcommand.split())
    process = slicer.util.launchConsoleProcess(args)
    process.wait()
    return process.stdout.read()

  def projects(self):
    return self.gcloud("projects list --format=value(PROJECT_ID)").split("\n")

  def datasets(self, project):
    return self.gcloud(f"--project {project} healthcare datasets list --format=value(ID,LOCATION)").split("\n")

  def dicomStores(self, project, dataset):
    return self.gcloud(f"--project {project} healthcare dicom-stores list --dataset {dataset} --format=value(ID)").split("\n")

  def token(self):
    return self.gcloud("auth print-access-token").strip()


#
# DICOMwebBrowserLogic
#

class DICOMwebBrowserLogic(ScriptedLoadableModuleLogic):
  """This class should implement all the actual
  computation done by your module.  The interface
  should be such that other python code can import
  this class and make use of the functionality without
  requiring an instance of the Widget
  """

  def __init__(self):
    pass


  def hasImageData(self, volumeNode):
    """This is a dummy logic method that
    returns true if the passed in volume
    node has valid image data
    """
    if not volumeNode:
      print('no volume node')
      return False
    if volumeNode.GetImageData() == None:
      print('no image data')
      return False
    return True

  def delayDisplay(self, message, msec=1000):
    #
    # logic version of delay display
    #
    print(message)
    self.info = qt.QDialog()
    self.infoLayout = qt.QVBoxLayout()
    self.info.setLayout(self.infoLayout)
    self.label = qt.QLabel(message, self.info)
    self.infoLayout.addWidget(self.label)
    qt.QTimer.singleShot(msec, self.info.close)
    self.info.exec_()

  def takeScreenshot(self, name, description, type=-1):
    # show the message even if not taking a screen shot
    self.delayDisplay(description)

    if self.enableScreenshots == 0:
      return

    lm = slicer.app.layoutManager()
    # switch on the type to get the requested window
    widget = 0
    if type == -1:
      # full window
      widget = slicer.util.mainWindow()
    elif type == slicer.qMRMLScreenShotDialog().FullLayout:
      # full layout
      widget = lm.viewport()
    elif type == slicer.qMRMLScreenShotDialog().ThreeD:
      # just the 3D window
      widget = lm.threeDWidget(0).threeDView()
    elif type == slicer.qMRMLScreenShotDialog().Red:
      # red slice window
      widget = lm.sliceWidget("Red")
    elif type == slicer.qMRMLScreenShotDialog().Yellow:
      # yellow slice window
      widget = lm.sliceWidget("Yellow")
    elif type == slicer.qMRMLScreenShotDialog().Green:
      # green slice window
      widget = lm.sliceWidget("Green")

    # grab and convert to vtk image data
    qpixMap = qt.QPixmap().grabWidget(widget)
    qimage = qpixMap.toImage()
    imageData = vtk.vtkImageData()
    slicer.qMRMLUtils().qImageToVtkImageData(qimage, imageData)

    annotationLogic = slicer.modules.annotations.logic()
    annotationLogic.CreateSnapShot(name, description, type, self.screenshotScaleFactor, imageData)

  def run(self, inputVolume, outputVolume, enableScreenshots=0, screenshotScaleFactor=1):
    """
    Run the actual algorithm
    """

    self.delayDisplay('Running the aglorithm')

    self.enableScreenshots = enableScreenshots
    self.screenshotScaleFactor = screenshotScaleFactor

    self.takeScreenshot('DICOMwebBrowser-Start', 'Start', -1)

    return True


class DICOMwebBrowserTest(unittest.TestCase):
  """
  This is the test case for your scripted module.
  """

  def delayDisplay(self, message, msec=1000):
    """This utility method displays a small dialog and waits.
    This does two things: 1) it lets the event loop catch up
    to the state of the test so that rendering and widget updates
    have all taken place before the test continues and 2) it
    shows the user/developer/tester the state of the test
    so that we'll know when it breaks.
    """
    print(message)
    self.info = qt.QDialog()
    self.infoLayout = qt.QVBoxLayout()
    self.info.setLayout(self.infoLayout)
    self.label = qt.QLabel(message, self.info)
    self.infoLayout.addWidget(self.label)
    qt.QTimer.singleShot(msec, self.info.close)
    self.info.exec_()

  def setUp(self):
    """ Do whatever is needed to reset the state - typically a scene clear will be enough.
    """
    slicer.mrmlScene.Clear(0)

  def runTest(self):
    import traceback
    """Run as few or as many tests as needed here.
    """
    self.setUp()
    self.testBrowserDownloadAndLoad()

  def testBrowserDownloadAndLoad(self):
    from random import randint

    self.delayDisplay("Starting the test")
    widget = DICOMwebBrowserWidget(None)
    widget.showBrowser()
    widget.connectToServer()

    browserWindow = widget.browserWidget
    collectionsCombobox = browserWindow.findChildren('QComboBox')[0]
    print('Number of collections: {}'.format(collectionsCombobox.count))
    if collectionsCombobox.count > 0:
      collectionsCombobox.setCurrentIndex(randint(0, collectionsCombobox.count - 1))
      currentCollection = collectionsCombobox.currentText
      if currentCollection != '':
        print('connected to the server successfully')
        print('current collection: {}'.format(currentCollection))

      tableWidgets = browserWindow.findChildren('QTableWidget')

      patientsTable = tableWidgets[0]
      if patientsTable.rowCount > 0:
        selectedRow = randint(0, patientsTable.rowCount - 1)
        selectedPatient = patientsTable.item(selectedRow, 0).text()
        if selectedPatient != '':
          print('selected patient: {}'.format(selectedPatient))
          patientsTable.selectRow(selectedRow)

        studiesTable = tableWidgets[1]
        if studiesTable.rowCount > 0:
          selectedRow = randint(0, studiesTable.rowCount - 1)
          selectedStudy = studiesTable.item(selectedRow, 0).text()
          if selectedStudy != '':
            print('selected study: {}'.format(selectedStudy))
            studiesTable.selectRow(selectedRow)

          seriesTable = tableWidgets[2]
          if seriesTable.rowCount > 0:
            selectedRow = randint(0, seriesTable.rowCount - 1)
            selectedSeries = seriesTable.item(selectedRow, 0).text()
            if selectedSeries != '':
              print('selected series to download: {}'.format(selectedSeries))
              seriesTable.selectRow(selectedRow)

            pushButtons = browserWindow.findChildren('QPushButton')
            for pushButton in pushButtons:
              toolTip = pushButton.toolTip
              if toolTip[16:20] == 'Load':
                loadButton = pushButton

            if loadButton != None:
              loadButton.click()
            else:
              print('could not find Load button')
    else:
      print("Test Failed. No collection found.")
    scene = slicer.mrmlScene
    self.assertEqual(scene.GetNumberOfNodesByClass('vtkMRMLScalarVolumeNode'), 1)
    self.delayDisplay('Browser Test Passed!')
