#-----------------------------------------------------------------------------
set(MODULE_NAME DICOMwebBrowser)

#-----------------------------------------------------------------------------
set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
  )

set(MODULE_PYTHON_RESOURCES
  Resources/Icons/downloadAndIndex.png
  Resources/Icons/downloadAndLoad.png
  Resources/Icons/DICOMWebBrowser.png
  Resources/Icons/stored.png
  Resources/Icons/download.png
  Resources/Icons/cancel.png
  )

#-----------------------------------------------------------------------------
slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
  WITH_GENERIC_TESTS
  )
