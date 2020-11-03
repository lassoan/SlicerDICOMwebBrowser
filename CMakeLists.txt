cmake_minimum_required(VERSION 2.8.9)

project(DICOMwebBrowser)

#-----------------------------------------------------------------------------
set(EXTENSION_HOMEPAGE "https://github.com/lassoan/SlicerDICOMwebBrowser#dicomwebbrowser")
set(EXTENSION_CATEGORY "Informatics")
set(EXTENSION_CONTRIBUTORS "Andras Lasso (PerkLab, Queen's), Alireza Mehrtash (Brigham and Women's Hospital), Andrey Fedorov (Brigham and Women's Hospital)")
set(EXTENSION_DESCRIPTION "Browse and download data from DICOMweb servers into 3D Slicer.")
set(EXTENSION_ICONURL "https://raw.githubusercontent.com/lassoan/SlicerDICOMwebBrowser/main/DICOMwebBrowser.png")
set(EXTENSION_SCREENSHOTURLS "https://raw.githubusercontent.com/lassoan/SlicerDICOMwebBrowser/main/Docs/Screenshot01.png")

#-----------------------------------------------------------------------------
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

#-----------------------------------------------------------------------------
add_subdirectory(DICOMwebBrowser)

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_CPACK})
