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
package com.ibm.websphere.samples.pbw.war;

import java.io.IOException;

import javax.inject.Inject;
import javax.inject.Named;
import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.pbw.bean.CatalogMgr;
import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * Servlet to handle image actions.
 */
@Named(value = "image")
@WebServlet("/servlet/ImageServlet")
public class ImageServlet extends HttpServlet {
	/**
	 * 
	 */
	private static final long serialVersionUID = 1L;

	@Inject
	private CatalogMgr catalog;

	/**
	 * Servlet initialization.
	 */
	public void init(ServletConfig config) throws ServletException {
		super.init(config);
	}

	/**
	 * Process incoming HTTP GET requests
	 *
	 * @param request
	 *            Object that encapsulates the request to the servlet
	 * @param response
	 *            Object that encapsulates the response from the servlet
	 */
	public void doGet(javax.servlet.http.HttpServletRequest request,
			javax.servlet.http.HttpServletResponse response) throws ServletException, IOException {
		performTask(request, response);
	}

	/**
	 * Process incoming HTTP POST requests
	 *
	 * @param request
	 *            Object that encapsulates the request to the servlet
	 * @param response
	 *            Object that encapsulates the response from the servlet
	 */
	public void doPost(javax.servlet.http.HttpServletRequest request,
			javax.servlet.http.HttpServletResponse response) throws ServletException, IOException {
		performTask(request, response);
	}

	/**
	 * Main service method for ImageServlet
	 *
	 * @param request
	 *            Object that encapsulates the request to the servlet
	 * @param response
	 *            Object that encapsulates the response from the servlet
	 */
	private void performTask(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
		String action = null;

		action = req.getParameter("action");
		Util.debug("action=" + action);

		if (action.equals("getimage")) {
			String inventoryID = req.getParameter("inventoryID");

			byte[] buf = catalog.getItemImageBytes(inventoryID);
			if (buf != null) {
				resp.setContentType("image/jpeg");
				resp.getOutputStream().write(buf);
			}
		}
	}
}