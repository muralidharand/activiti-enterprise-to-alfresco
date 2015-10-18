#!/usr/bin/python
import os, sys
import json
import zipfile
import xml.etree.ElementTree as ET
from constants import *
from converters import *

if len(sys.argv) < 4 or "--help" in sys.argv:
  print "Use:"
  print "   to-share.py <exported.bpmn> <exported-app.zip> <namespace prefix> [module name] [output dir]"
  print ""
  print " eg to-share.py exported.bpmn20.xml exported.zip sample-wf"
  sys.exit(1)

workflow = sys.argv[1]
app_zip  = sys.argv[2]
namespace = sys.argv[3]
module_name = sys.argv[4] if len(sys.argv) > 4 else "FIXME"
output_dir  = sys.argv[5] if len(sys.argv) > 5 else os.path.curdir

# Sanity check our options
with open(workflow, "r") as wf_file:
  wf_xml = wf_file.read()
if not wf_xml.startswith("<?xml version='1.0'") or not bpmn20_ns in wf_xml:
  print "Error - %s isn't a BPMN 2.0 workflow definition" % workflow
  sys.exit(1)

if os.path.exists(output_dir):
  print "Output files will be placed in '%s'" % output_dir
else:
  print "Error - desired output folder not found: %s" % output_dir
  print ""
  print "Please create or correct the path, and re-run"
  sys.exit(1)

if ":" in namespace or "_" in namespace:
  print "Namespace should be of the form namespace not name:space or name_space"
  print ""
  print "  eg sample-wf"
  print ""
  print "Which will map to sample-wf:Form1 sample-wf:Form2 etc"
  print ""
  print "Namespace should not contain a : as one will be added"
  print "Namespace should not contain a _ as that confuses the Share forms engine"
  sys.exit(1)

# Open the Activiti exported zip
app = zipfile.ZipFile(app_zip, "r")

# Setup for BPMN parsing
for prefix,ns in xml_namespaces.items():
   ET.register_namespace(prefix,ns)

# Look for Forms in the Workflow
tree = ET.parse(workflow)
wf = tree.getroot()
form_refs = wf.findall("**/[@{%s}formKey]" % activiti_ns)

if len(form_refs) == 0:
   print "No forms found in your workflow"
   print "The Workflow BPMN 2.0 XML file should be fine to be loaded into"
   print " your normal Alfresco instance and used as-is"

# Decide on the short namespace forms
namespace_sf = namespace + ":"
namespace_uri = "Activit_Exported_%s" % namespace
model_name = "%s:model" % namespace

# Check we only had one process
process_id = []
process_tag_name = "{%s}process" % bpmn20_ns
for elem in wf:
   if elem.tag == process_tag_name:
      process_id.append(elem.attrib["id"])
if len(process_id) == 1:
   process_id = process_id[0]
else:
   print "Expected 1 process definition in your BPMN file, but found %d" % (len(process_id))
   print "Only one process per file is supported"
   print "Found: %s" % " ".join(process_id)
   sys.exit(1)

# Start building out model and form config
model = ModelOutput(output_dir, module_name)
model.begin(model_name, namespace_uri, namespace)

context = ContextOutput(output_dir, module_name)
context.begin(model_name, namespace_uri, namespace)

share_config = ShareConfigOutput(output_dir, module_name)
share_config.begin(model_name, namespace_uri, namespace)

##########################################################################

def get_alfresco_task_types(task_tag):
   "Returns the Alfresco model type and Share form type for a given task"
   if "{" in task_tag and "}" in task_tag:
      tag_ns = task_tag.split("{")[1].split("}")[0]
      tag_name = task_tag.split("}")[1]
      mt = model_types.get(tag_ns, None)
      if not mt:
         print "Error - no tag mappings found for namespace %s" % tag_ns
         print "Unable to process %s" % task_tag
         sys.exit(1)
      alf_type = mt.get(tag_name, None)
      if not alf_type:
         print "Error - no tag mappings found for tag %s" % tag_name
         print "Unable to process %s" % task_tag
         sys.exit(1)
      # Is it a start task?
      is_start_task = False
      if alf_type == start_task:
         is_start_task = True
      return (alf_type, is_start_task)
   print "Error - Activiti task with form but no namespace - %s" % task_tag
   sys.exit(1)

# TODO Handle recursion for the share config bits
def process_fields(fields, share_form):
   # Model associations can only be done after all the fields are processed
   associations = []
   model.write("       <properties>\n")
   # Process most of the form now
   handle_fields(fields, share_form, associations)
   # Finish off the model bits
   model.write("       </properties>\n")
   if associations:
      model.write("       <associations>\n")
      for assoc in associations:
         model.write("         <association name=\"%s\">\n" % assoc[0])
         if assoc[1]:
            model.write("           <title>%s</title>\n" % assoc[1])
         model.write("           <source>\n")
         model.write("             <mandatory>%s</mandatory>\n" % str(assoc[2][0]).lower())
         model.write("             <many>%s</many>\n" % str(assoc[2][1]).lower())
         model.write("           </source>\n")
         model.write("           <target>\n")
         model.write("             <class>%s</class>\n" % str(assoc[2][2]).lower())
         model.write("             <mandatory>%s</mandatory>\n" % str(assoc[2][3]).lower())
         model.write("             <many>%s</many>\n" % str(assoc[2][4]).lower())
         model.write("           </target>\n")
         model.write("         </association>\n")
      model.write("       </associations>\n")

def handle_fields(fields, share_form, associations):
   for field in fields:
      if field.get("fieldType","") == "ContainerRepresentation":
         # Recurse, we don't care about container formatting at this time
         # TODO Track the containers into sets
         for f in field["fields"]:
             if f in ("1","2","3","4"):
                handle_fields(field["fields"][f], share_form, associations)
             else:
                print "Non-int field in fields '%s'" % f
                print json.dumps(field, sort_keys=True, indent=4, separators=(',', ': '))
      else:
         # Handle the form field
         field_id = field["id"].replace(u"\u2019","")
         print "%s -> %s" % (field_id,field.get("name",None))

         alf_id = "%s:%s" % (namespace, field_id)
         name = field.get("name", None)
         ftype = field["type"]

         # Check how to convert
         if not property_types.has_key(ftype) and not assoc_types.has_key(ftype):
            print "Warning - unhandled type %s" % ftype
            print json.dumps(field, sort_keys=True, indent=4, separators=(',', ': '))
            ftype = "text"
         alf_type = property_types.get(ftype, None)
         options = field.get("options",None)

         # TODO Handle required, default values, multiples etc

         if alf_type:
            model.write("         <property name=\"%s\">\n" % alf_id)
            if name:
               model.write("           <title>%s</title>\n" % name)
            model.write("           <type>%s</type>\n" % alf_type)
            if ftype == "readonly-text":
               model.write("           <default>%s</default>\n" % field.get("value",""))
            if options:
               model.write("           <constraints>\n")
               model.write("             <constraint type=\"LIST\">\n")
               model.write("               <parameter name=\"allowedValues\"><list>\n")
               for opt in options:
                  model.write("                 <value>%s</value>\n" % opt["name"])
               model.write("               </list></parameter>\n")
               model.write("             </constraint>\n")
               model.write("           </constraints>\n")
            model.write("         </property>\n")
         if assoc_types.has_key(ftype):
            associations.append((alf_id,name,assoc_types.get(ftype)))

         # Record the Share "field-visibility" for this
         share_form.record_visibility(alf_id)

         # Record the appearance details
         appearance = "<field id=\"%s\"" % alf_id
         if field.has_key("name"):
            appearance += " label=\"%s\"" % field.get("name")
         appearance += ">\n"

         if ftype == "readonly-text":
             appearance += "  <control template=\"/org/alfresco/components/form/controls/readonly.ftl\">\n"
             appearance += "    <control-param name=\"value\">%s</control-param>\n" % field.get("value","")
             appearance += "  </control>\n"
         if ftype in ("radio-buttons","dropdown") and options:
             appearance += "  <control template=\"/org/alfresco/components/form/controls/selectone.ftl\">\n"
             appearance += "    <control-param name=\"options\">%s</control-param>\n" % ",".join([o["name"] for o in options])
             appearance += "  </control>\n"

         appearance += "</field>\n"
         share_form.record_appearance(appearance)
         # TODO Use this to finish getting and handling the other options
         #print json.dumps(field, sort_keys=True, indent=4, separators=(',', ': '))

# Process the forms
for form_num in range(len(form_refs)):
   form_elem = form_refs[form_num]
   form_ref = form_elem.get("{%s}formKey" % activiti_ns)
   form_new_ref = "%s:Form%d" % (namespace, form_num)
   tag_name = form_elem.tag.replace("{%s}" % bpmn20_ns, "")
   print ""
   print "Processing form %s for %s / %s" % (form_ref, tag_name, form_elem.get("id","(n/a)"))

   # Update the form ID on the workflow
   form_elem.set("{%s}formKey" % activiti_ns, form_new_ref)

   # Work out what type to make it
   alf_task_type, is_start_task = get_alfresco_task_types(form_elem.tag)
   alf_task_title = form_elem.attrib.get("name",None)

   # Locate the JSON for it
   form_json_name = None
   for f in app.namelist():
      if f.startswith("form-models/") and f.endswith("-%s.json" % form_ref):
         form_json_name = f
   if form_json_name:
      print " - Reading from %s" % form_json_name
   else:
      print "Error - %s doesn't have a form-model for %s" % (app_zip, form_ref)
      sys.exit(1)

   # Read the JSON from the zip
   form_json = json.loads(app.read(form_json_name))

   # Prepare for the Share Config part
   share_form = ShareFormConfigOutput(share_config, process_id, form_new_ref)

   # Process as a type
   model.write("    <type name=\"%s\">\n" % form_new_ref)
   if alf_task_title:
      model.write("       <title>%s</title>\n" % alf_task_title)
   model.write("       <parent>%s</parent>\n" % alf_task_type)

   process_fields(form_json["fields"], share_form)

   model.write("    </type>\n")

   # Do the Share Config conversion + output
   if is_start_task:
      share_form.write_out(True, True)
   share_form.write_out(is_start_task, False)

##########################################################################

# Sort out things that Activiti Enterprise is happy with, but which
#  Activiti-in-Alfresco won't like
BPMNFixer.fix_all(wf)

# Output the updated workflow
updated_workflow = os.path.join(output_dir, "%s.bpmn20.xml" % module_name)
tree.write(updated_workflow, encoding="UTF-8", xml_declaration=True)

# Finish up
model.complete()
context.complete()
share_config.complete()

# Report as done
print ""
print "Conversion completed!"
print "Files generated are:"
for f in (model,context,share_config,updated_workflow):
   if hasattr(f,"outfile"):
      print "  %s" % f.outfile
   else:
      print "  %s" % f
