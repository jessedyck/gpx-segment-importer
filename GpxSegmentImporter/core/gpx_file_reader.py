# Initialize Qt resources from file resources.py
from xml.etree import ElementTree
from qgis.core import (QgsPoint, QgsCoordinateReferenceSystem, QgsMessageLog)
from .datatype_definition import (DataTypeDefinition, DataTypes)
from .gpx_feature_builder import GpxFeatureBuilder
from .geom_tools import GeomTools
import os


class GpxFileReader:
    """ Reads gpx files and assembles vector layers """

    def __init__(self):
        self.attribute_definitions = list()
        self.namespace = None
        self.error_message = ''
        self.track_count = 0
        self.track_segment_count = 0
        self.track_point_count = 0
        self.equal_coordinates = 0

    def get_table_data(self, file_path):
        """ Reads the first GPX track point and create datatype definitions from the available attributes """

        self.attribute_definitions = list()
        self.error_message = ''

        tree = ElementTree.parse(file_path)
        root = tree.getroot()

        # https://stackoverflow.com/questions/1953761/accessing-xmlns-attribute-with-python-elementree
        if root.tag[0] == "{":
            uri, ignore, tag = root.tag[1:].partition("}")
            self.namespace = {'gpx': uri}

        track = root.find('gpx:trk', self.namespace)
        if track is not None:
            track_segment = track.find('gpx:trkseg', self.namespace)
            if track_segment is not None:
                track_points = track_segment.findall('gpx:trkpt', self.namespace)
                if len(track_points) > 0:
                    for track_point in track_points:
                        for child in track_point:
                            self.detect_attribute(child)
                else:
                    self.error_message = 'Cannot find trkpt-tag in GPX file'
            else:
                self.error_message = 'Cannot find trkseg-tag in GPX file'
        else:
            self.error_message = 'Cannot find trk-tag in GPX file'

        return True if self.error_message == '' else False

    def import_gpx_file(self, file_path, output_directory, attribute_select="Last", use_wgs84=True,
                        calculate_motion_attributes=False, overwrite=False):
        """ Imports the data from the GPX file and create the vector layer """

        if len(self.attribute_definitions) == 0:
            self.get_table_data(file_path)

        self.error_message = ''
        # self.did_log = False

        if calculate_motion_attributes:
            self.attribute_definitions.append(DataTypeDefinition('_a_index', DataTypes.Integer, True, ''))
            self.attribute_definitions.append(DataTypeDefinition('_b_index', DataTypes.Integer, True, ''))
            self.attribute_definitions.append(DataTypeDefinition('_distance', DataTypes.Double, True, ''))
            self.attribute_definitions.append(DataTypeDefinition('_duration', DataTypes.Double, True, ''))
            self.attribute_definitions.append(DataTypeDefinition('_speed', DataTypes.Double, True, ''))
            self.attribute_definitions.append(DataTypeDefinition('_elevation_diff', DataTypes.Double, True, ''))

        # Add attribute field for track type
        self.attribute_definitions.append(DataTypeDefinition('type', DataTypes.String, True, ''))

        tree = ElementTree.parse(file_path)
        root = tree.getroot()

        crs = QgsCoordinateReferenceSystem('EPSG:4326') if use_wgs84 else None

        vector_layer_builder = GpxFeatureBuilder(os.path.basename(file_path), self.attribute_definitions,
                                                 attribute_select, crs)

        self.equal_coordinates = 0
        self.track_count = 0
        self.track_segment_count = 0
        self.track_point_count = 0

        for track in root.findall('gpx:trk', self.namespace):
            self.track_count += 1

            # Extract the the 'type' value from the parent trk
            trackType = None
            trackTypeList = track.findall('gpx:type', self.namespace)
            if len(trackTypeList) != 0 :
                trackType = trackTypeList[0].text

            # if self.did_log is False:
            #     QgsMessageLog.logMessage( trackType, 'GPX Segment Importer' )
            #     self.did_log = True

            for track_segment in track.findall('gpx:trkseg', self.namespace):
                self.track_segment_count += 1
                prev_track_point = None
                prev_track_point_index = -1

                for track_point in track_segment.findall('gpx:trkpt', self.namespace):
                    self.track_point_count += 1

                    if prev_track_point is not None:
                        elevation_a_element = prev_track_point.find('gpx:ele', self.namespace)
                        elevation_b_element = track_point.find('gpx:ele', self.namespace)
                        elevation_a = float(elevation_a_element.text) if (elevation_a_element is not None) else None
                        elevation_b = float(elevation_b_element.text) if (elevation_b_element is not None) else None

                        previous_point = QgsPoint(
                            float(prev_track_point.get('lon')),
                            float(prev_track_point.get('lat')),
                            elevation_a if (elevation_a is not None) else None
                        )
                        new_point = QgsPoint(
                            float(track_point.get('lon')),
                            float(track_point.get('lat')),
                            elevation_b if (elevation_b is not None) else None
                        )

                        if GeomTools.is_equal_coordinate(previous_point, new_point):
                            self.equal_coordinates += 1
                            continue

                        # add a feature with first/last/both attributes
                        attributes = dict()
                        if attribute_select == 'First':
                            self.add_attributes(attributes, prev_track_point, '')
                        elif attribute_select == 'Last':
                            self.add_attributes(attributes, track_point, '')
                        elif attribute_select == 'Both':
                            self.add_attributes(attributes, prev_track_point, 'a_')
                            self.add_attributes(attributes, track_point, 'b_')

                        # Adds the the 'type' value from the parent trk to each trkpt
                        attributes['type'] = trackType

                        if calculate_motion_attributes:
                            attributes['_a_index'] = prev_track_point_index
                            attributes['_b_index'] = self.track_point_count - 1
                            attributes['_distance'] = GeomTools.distance(previous_point, new_point, crs)

                            time_a = DataTypes.create_date(prev_track_point.find('gpx:time', self.namespace).text)
                            time_b = DataTypes.create_date(track_point.find('gpx:time', self.namespace).text)

                            if time_a is not None or time_b is not None:
                                attributes['_duration'] = GeomTools.calculate_duration(time_a, time_b)
                                attributes['_speed'] = GeomTools.calculate_speed(time_a, time_b, previous_point,
                                                                                 new_point, crs)

                            if elevation_a is not None or elevation_b is not None:
                                attributes['_elevation_diff'] = elevation_b - elevation_a

                        vector_layer_builder.add_feature([previous_point, new_point], attributes)

                    prev_track_point = track_point
                    prev_track_point_index = self.track_point_count - 1

        vector_layer = vector_layer_builder.save_layer(output_directory, overwrite)
        if vector_layer_builder.error_message != '':
            self.error_message = vector_layer_builder.error_message
            print(self.error_message)

        return vector_layer

    def detect_attribute(self, element):
        """ Either detects the attribute or recursively finds child elements """

        if len(element) == 0:  # only elements without children
            if element.get('key') is not None:
                new_definition = DataTypeDefinition(
                    element.get('key'),
                    DataTypes.detect_data_type(element.get('value')),
                    element.get('value') is not None and element.get('value') != '',
                    element.get('value'))
            else:
                new_definition = DataTypeDefinition(
                    self.normalize(element.tag),
                    DataTypes.detect_data_type(element.text),
                    element.text is not None and element.text != '',
                    element.text)
            if new_definition:
                detected = False
                for definition in self.attribute_definitions:
                    if definition.attribute_key == new_definition.attribute_key:
                        detected = True
                        break
                if detected is False:
                    self.attribute_definitions.append(new_definition)
        for child in element:
            self.detect_attribute(child)

    def add_attributes(self, attributes, element, key_prefix):
        """ Reads and adds attributes to the feature """

        if len(element) == 0:  # only elements without children
            try:
                # check if attribute value is available
                if element.get('key') is not None:
                    attribute = self._get_attribute_definition(element.get('key'))
                    if attribute is None:
                        return
                    attribute.example_value = element.get('value')
                else:
                    attribute = self._get_attribute_definition(self.normalize(element.tag))
                    if attribute is None:
                        return
                    attribute.example_value = element.text

                if attribute.datatype is DataTypes.Integer and DataTypes.value_is_int(attribute.example_value) or \
                        attribute.datatype is DataTypes.Double and \
                        DataTypes.value_is_double(attribute.example_value) or \
                        attribute.datatype is DataTypes.String:
                    attributes[key_prefix + attribute.attribute_key_modified] = attribute.example_value
                elif attribute.datatype is DataTypes.Boolean and DataTypes.value_is_boolean(attribute.example_value):
                    attributes[key_prefix + attribute.attribute_key_modified] = str(attribute.example_value)
            except KeyError:
                pass
                # print('KeyError while reading attribute ' + self.normalize(extension.tag))
        for child in element:
            self.add_attributes(attributes, child, key_prefix)

    def _get_attribute_definition(self, key):
        for attribute in self.attribute_definitions:
            if key == attribute.attribute_key:
                return attribute
        return None

    @staticmethod
    def normalize(name):
        if name[0] == '{':
            uri, tag = name[1:].split('}')
            return tag
        else:
            return name
