//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2001,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//

var isNav4, isIE;
var coll = "";
var styleObj = "";
if (parseInt(navigator.appVersion) >= 4) {
	if (navigator.appName == "Netscape") {
		isNav4 = true;
	} else {
		isIE = true;
		coll = "all.";
		styleObj = ".style";
	}
}

function refresh() {
	if (refreshTree.value == "true")
		parent.navigation_tree.location.reload(true);
}

var numchecks = 0;
var allchecked = false;
var multiall = new Array();
function updateCheckAll(theForm, chkname) {
	var temp;
	var alltemp = 0;
	var formlen = theForm.length;
	if (chkname != null) {
		var allchkname = chkname.substring(0, chkname.indexOf("CheckBox"));
		allchkname = "allchecked" + allchkname;
	}
	for (var i = 0; i < formlen; i++) {
		var theitem = theForm.elements[i].name;
		var ischeck = theitem.indexOf("selectedObjectIds", 0) + 1; /* simple string search on checkbox consistent name, you change it to deleteID or whatever */
		var allcurcheck = theitem.indexOf(allchkname, 0) + 1;
		if (allcurcheck > 0) {
			alltemp = i;
		}
		if (chkname == null) {

			if (ischeck > 0) {
				if (allchecked != true) {
					theForm.elements[i].checked = true;
					temp = true;
				} else {
					theForm.elements[i].checked = false;
					temp = false;

				}
			}

			var appitem = theForm.elements[i].name;
			var appcheck = appitem.indexOf("checkBoxes", 0) + 1;
			if ((appitem == "checkBoxes1") || (appitem == "checkBoxes2")) {
				appcheck = 0;
			}

			if (appcheck > 0) {
				if (allchecked != true) {
					theForm.elements[i].checked = true;
					temp = true;

				} else {
					theForm.elements[i].checked = false;
					temp = false;

				}
			}

		} else {

			var curitem = theForm.elements[i].name;
			//var curcheck = curitem.indexOf(chkname[0].name,0) + 1;

			var curcheck = curitem.indexOf(chkname, 0) + 1;

			if (curcheck > 0) {
				if ((allchecked != true) && (multiall[allchkname] != true)) {
					theForm.elements[i].checked = true;
					temp = true;

				} else {
					theForm.elements[i].checked = false;
					temp = false;

				}
			}

		}

	}

	if (temp == true) {
		if (chkname == null) {
			allchecked = true;
			theForm.allchecked.checked = true;
		} else {
			multiall[allchkname] = true;
			theForm.elements[alltemp].checked = true;
		}
	} else {
		if (chkname == null) {
			allchecked = false;
			theForm.allchecked.checked = false;
		} else {
			multiall[allchkname] = false;
			theForm.elements[alltemp].checked = false;
		}
	}

}

function checkChecks(theForm, chkname) {
	var checkednum = 0;
	var uncheckednum = 0;
	var formlen = theForm.length;

	for (var i = 0; i < formlen; i++) {
		var theitem = theForm.elements[i].name;
		var ischeck = theitem.indexOf("selectedObjectIds", 0) + 1;
		var appitem = theForm.elements[i].name;
		var appcheck = appitem.indexOf("checkBoxes", 0) + 1;
		if ((appitem == "checkBoxes1") || (appitem == "checkBoxes2")) {
			appcheck = 0;
		}

		if (chkname != null) {
			var curcheck = theitem.indexOf(chkname.name, 0) + 1;

		}

		if (ischeck > 0) {
			if (theForm.elements[i].checked == true) {
				checkednum += 1;
			} else {
				uncheckednum += 1;
			}
		}
		if (curcheck > 0) {
			if (theForm.elements[i].checked == true) {
				checkednum += 1;
			} else {
				uncheckednum += 1;

			}

		}
		if (appcheck > 0) {
			if (theForm.elements[i].checked == true) {
				checkednum += 1;
			} else {
				uncheckednum += 1;
			}

		}

	}

	if (allchecked == true) {

		if (uncheckednum > 0) {
			allchecked = false;
			theForm.allchecked.checked = false;
		}
	} else {
		if (uncheckednum == 0) {
			allchecked = true;
			theForm.allchecked.checked = true;

		}
	}

}
