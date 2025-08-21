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
// (C) COPYRIGHT International Business Machines Corp., 2011
// All Rights Reserved * Licensed Materials - Property of IBM
//

if (document.images) {
	menu1s = new Image();
	menu1s.src = "resources/images/tab_flowers_s.gif";
	menu2s = new Image();
	menu2s.src = "resources/images/tab_veggies_s.gif";
	menu3s = new Image();
	menu3s.src = "resources/images/tab_trees_s.gif";
	menu4s = new Image();
	menu4s.src = "resources/images/tab_accessories_s.gif";

	menu1u = new Image();
	menu1u.src = "resources/images/tab_flowers_u.gif";
	menu2u = new Image();
	menu2u.src = "resources/images/tab_veggies_u.gif";
	menu3u = new Image();
	menu3u.src = "resources/images/tab_trees_u.gif";
	menu4u = new Image();
	menu4u.src = "resources/images/tab_accessories_u.gif";

	}

	function selectMenu (imgName) {
		if (top.banner.document.images) {
			top.banner.document[imgName].src = eval(imgName + "s.src");
		}
	}
	
	function deselectMenu (imgName) {
		if (top.banner.document.images) {
			top.banner.document[imgName].src = eval(imgName + "u.src");
		}
	}
	
	function useBill()
    {
         var billName = document.getElementById ("orderinfo:bname");
         var billAddr1 = document.getElementById ("orderinfo:baddr1");
         var billAddr2 = document.getElementById ("orderinfo:baddr2");
         var billCity = document.getElementById ("orderinfo:bcity");
         var billState = document.getElementById ("orderinfo:bstate");
         var billZip = document.getElementById ("orderinfo:bzip");
         var billPhone = document.getElementById ("orderinfo:bphone");
         var shipName = document.getElementById ("orderinfo:sname");
         var shipAddr1 = document.getElementById ("orderinfo:saddr1");
         var shipAddr2 = document.getElementById ("orderinfo:saddr2");
         var shipCity = document.getElementById ("orderinfo:scity");
         var shipState = document.getElementById ("orderinfo:sstate");
         var shipZip = document.getElementById ("orderinfo:szip");
         var shipPhone = document.getElementById ("orderinfo:sphone");
         var shipIsBill = document.getElementById ("orderinfo:shipisbill");

         if (shipIsBill.checked) {
             shipName.value = billName.value;
             shipAddr1.value = billAddr1.value;
             shipAddr2.value = billAddr2.value;
             shipCity.value = billCity.value;
             shipState.value = billState.value;
             shipZip.value = billZip.value;
             shipPhone.value = billPhone.value;
         }

         else {
      	     shipName.value = "";
             shipAddr1.value = "";
             shipAddr2.value = "";
             shipCity.value = "";
             shipState.value = "";
             shipZip.value = "";
             shipPhone.value = "";
         }
    }